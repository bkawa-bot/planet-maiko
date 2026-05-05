from flask import Blueprint, jsonify, request
from planet_maiko.database import db, iso_utc
from planet_maiko.models.agent_profile import AgentProfile
from planet_maiko.agents.profiles import create_profile

profiles_bp = Blueprint("profiles", __name__)


@profiles_bp.route("/profiles", methods=["GET"])
def list_profiles():
    """List agent profiles with each one's 3 most-recent completed tasks.

    Query params:
        archived=true: include archived profiles in the response.
    """
    from planet_maiko.models.task import Task

    include_archived = request.args.get("archived", "false").lower() == "true"
    role = request.args.get("role")
    repo = request.args.get("repo")

    query = AgentProfile.query
    if not include_archived:
        query = query.filter((AgentProfile.archived == False) | (AgentProfile.archived == None))
    if role:
        query = query.filter(AgentProfile.role == role)
    if repo:
        # Either scoped to this repo OR global (scope_repo IS NULL) —
        # global agents cover any repo.
        query = query.filter(
            (AgentProfile.scope_repo == repo) | (AgentProfile.scope_repo.is_(None))
        )
    profiles = query.order_by(AgentProfile.tasks_completed.desc()).all()
    if not profiles:
        return jsonify([])

    # Bulk-fetch recent done work for every profile in one pass —
    # union of user-owed Tasks (status=done) AND pack-owned AgentJobs
    # (status=done). Post-Stage D most review/cartograph/investigation
    # runs finish as AgentJobs, so if we only queried Tasks the recent
    # section would look empty even on an agent who just landed five
    # cartographs. Merge both, sort by finish time, keep 3 per agent.
    from planet_maiko.models.agent_job import AgentJob
    ids = [p.id for p in profiles]
    recent_tasks = (
        Task.query
        .filter(Task.assigned_agent_id.in_(ids))
        .filter(Task.status == "done")
        .order_by(Task.updated_at.desc())
        .limit(500)
        .all()
    )
    recent_jobs = (
        AgentJob.query
        .filter(AgentJob.agent_profile_id.in_(ids))
        .filter(AgentJob.status == "done")
        .order_by(AgentJob.finished_at.desc())
        .limit(500)
        .all()
    )
    by_agent = {p.id: [] for p in profiles}
    for t in recent_tasks:
        by_agent[t.assigned_agent_id].append({
            "id": t.id,
            "title": t.title,
            "type": t.type,
            "kind": "task",
            "sort_at": t.updated_at,
            "updated_at": iso_utc(t.updated_at),
            "has_artifact": bool((t.extra or {}).get("artifact")),
        })
    for j in recent_jobs:
        by_agent[j.agent_profile_id].append({
            "id": j.id,
            "title": j.title,
            "type": j.kind,
            "kind": "job",
            "sort_at": j.finished_at or j.updated_at,
            "updated_at": iso_utc(j.finished_at or j.updated_at),
            "has_artifact": bool(j.artifact),
        })
    # Sort each agent's list by finish time desc, keep top 3.
    for agent_id, items in by_agent.items():
        items.sort(key=lambda x: x["sort_at"] or 0, reverse=True)
        by_agent[agent_id] = [
            {k: v for k, v in i.items() if k != "sort_at"} for i in items[:3]
        ]

    out = []
    for p in profiles:
        d = p.to_dict()
        d["recent_tasks"] = by_agent.get(p.id, [])
        out.append(d)
    return jsonify(out)


@profiles_bp.route("/profiles/just-arrived", methods=["GET"])
def list_just_arrived():
    """Profiles whose arrival bio-gen has finished recently.

    Used by the global ArrivalWatcher to pop a celebratory modal
    once the LLM names + writes the bio for a freshly-created agent.
    The watcher polls this and dedupes locally in localStorage so a
    given agent only ever shows the modal once.

    Filters:
      - not archived
      - display_name != "Arriving…" (bio gen has resolved, even via
        the fallback random-name path)
      - created_at within last 30 min (cuts off old agents that
        recover_stale_arrivals rescues across a restart, plus keeps
        the polling response small)
    """
    from datetime import datetime, timezone, timedelta
    from planet_maiko.agents.profiles import ARRIVING_PLACEHOLDER

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    rows = (
        AgentProfile.query
        .filter((AgentProfile.archived == False) | (AgentProfile.archived == None))  # noqa: E712
        .filter(AgentProfile.display_name != ARRIVING_PLACEHOLDER)
        .filter(AgentProfile.created_at >= cutoff)
        .order_by(AgentProfile.created_at.desc())
        .limit(20)
        .all()
    )
    return jsonify([p.to_dict() for p in rows])


@profiles_bp.route("/profiles/<profile_id>", methods=["GET"])
def get_profile(profile_id):
    """Get a single agent profile."""
    profile = db.get_or_404(AgentProfile, profile_id)
    return jsonify(profile.to_dict())


VALID_ROLES = ("coding", "review", "investigation", "cartographer")


def _sanitize_specialty_ids(raw):
    """Drop anything that isn't a known CustomSkill ID so the list never
    holds dead references. Returns a clean list (may be empty)."""
    if not raw:
        return []
    if not isinstance(raw, (list, tuple)):
        return []
    from planet_maiko.models.custom_skill import CustomSkill
    ids = [str(x).strip() for x in raw if str(x).strip()]
    if not ids:
        return []
    existing = {s.id for s in CustomSkill.query.filter(CustomSkill.id.in_(ids)).all()}
    return [i for i in ids if i in existing]


@profiles_bp.route("/profiles", methods=["POST"])
def create_agent_profile():
    """Create a new agent profile (the arrival experience).

    Body fields (all optional):
      agent_id, display_name, avatar — cosmetic identity
      role          — "coding" (default) | "review" | "investigation" |
                      "cartographer". Drives runtime dispatch.
      scope_repo    — single-repo scope, e.g. "org/auth-service". Null
                      = global (e.g. a Detective for cross-repo work).
      instructions  — markdown, injected into every session this agent
                      runs. Equivalent to a per-agent AGENTS.md fragment.
      specialty_ids — list of CustomSkill IDs attached to this agent.
                      A run can pick one; no pick = base role only.
    """
    data = request.get_json(silent=True) or {}
    role = data.get("role") or "coding"
    if role not in VALID_ROLES:
        return jsonify({"error": f"invalid role: {role}"}), 400
    profile = create_profile(
        agent_id=data.get("agent_id", f"agent-{__import__('time').time_ns()}"),
        display_name=data.get("display_name"),
        avatar=data.get("avatar"),
        role=role,
        scope_repo=(data.get("scope_repo") or None),
        instructions=(data.get("instructions") or None),
        specialty_ids=_sanitize_specialty_ids(data.get("specialty_ids")),
    )
    return jsonify(profile.to_dict()), 201


@profiles_bp.route("/profiles/<profile_id>", methods=["PATCH"])
def update_profile(profile_id):
    """Update agent profile (rename, change avatar, role, scope, instructions, specialties)."""
    profile = db.get_or_404(AgentProfile, profile_id)
    data = request.get_json()
    if "display_name" in data:
        profile.display_name = data["display_name"]
    if "avatar" in data:
        profile.avatar = data["avatar"]
    if "flavor_text" in data:
        profile.flavor_text = data["flavor_text"]
    if "role" in data and data["role"] in VALID_ROLES:
        profile.role = data["role"]
    if "scope_repo" in data:
        # Empty string → null (global scope).
        profile.scope_repo = data["scope_repo"] or None
    if "instructions" in data:
        profile.instructions = data["instructions"] or None
    if "specialty_ids" in data:
        profile.specialty_ids = _sanitize_specialty_ids(data["specialty_ids"])
    db.session.commit()
    return jsonify(profile.to_dict())


@profiles_bp.route("/profiles/<profile_id>/archive", methods=["POST"])
def archive_profile(profile_id):
    """Archive an agent profile."""
    from datetime import datetime, timezone
    profile = db.get_or_404(AgentProfile, profile_id)
    profile.archived = True
    profile.archived_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(profile.to_dict())


@profiles_bp.route("/profiles/<profile_id>/unarchive", methods=["POST"])
def unarchive_profile(profile_id):
    """Unarchive an agent profile."""
    profile = db.get_or_404(AgentProfile, profile_id)
    profile.archived = False
    profile.archived_at = None
    db.session.commit()
    return jsonify(profile.to_dict())


@profiles_bp.route("/cards", methods=["GET"])
def list_cards():
    """Return all personality card archetypes.

    Frontend uses this to resolve agent.avatar (= card_id) into the
    full card metadata for rendering avatars and the baseball-card
    profile modal. Cached in-process; restart to pick up changes to
    cards.yaml.
    """
    from planet_maiko.agents.cards import load_cards
    return jsonify(load_cards())
