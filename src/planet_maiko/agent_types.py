"""AgentType seeding + backfill from CustomSkill.

This module owns two boot-time concerns:

  1. ensure_seed_agent_types — seeds the four built-in AgentType
     rows (coding / review / investigation / cartographer) on first
     boot. Reads the protocol body from the bundled prompt .md files.
     Idempotent — re-seeding on subsequent boots refreshes the bundled
     protocol_prompt on un-edited defaults so we can ship prompt
     updates without users having to delete their AgentType rows.
     Respects the deleted_at tombstone (a user who removed a default
     stays removed across boots).

  2. backfill_from_custom_skills — copies existing CustomSkill rows
     into Specialty (and, if a CustomSkill has its own protocol_prompt
     set, into AgentType as a custom type). Runs once per boot;
     skipped fast when target rows already exist by id. Lets a running
     install pick up the new tables without dropping data.

Both run from app.py's create_app() startup block, after db.create_all
+ the _PATCH_COLUMNS migration pass.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def get_agent_type(role):
    """Resolve a role string (AgentProfile.role / AgentJob.kind) to an
    AgentType row, or None.

    Honors deleted_at — a tombstoned type resolves to None so callers
    fall through to legacy behavior. Returns None silently on any
    DB failure so call sites don't need a try/except every time.

    The four built-ins (coding / review / investigation / cartographer)
    are seeded on boot, so the lookup hits for them too — there's no
    distinction between "built-in" and "custom" at the read API.
    """
    try:
        from planet_maiko.database import db
        from planet_maiko.models.agent_type import AgentType
        at = db.session.get(AgentType, role)
        if at is not None and at.deleted_at is None:
            return at
    except Exception:
        pass
    return None


def _resolve_role(kind_or_role):
    """Resolve a kind / task type / role string to an AgentType row.

    Uses orchestration.TYPE_TO_ROLE to map kinds → roles (so
    "pr_review" → "review", "cartograph" → "cartographer" etc.)
    before the AgentType lookup. Returns None on any miss.
    """
    try:
        from planet_maiko.orchestration import TYPE_TO_ROLE
    except Exception:
        TYPE_TO_ROLE = {}
    role = TYPE_TO_ROLE.get(kind_or_role, kind_or_role)
    return get_agent_type(role)


def auto_tag_insights_for(kind_or_role):
    """Tags that should be applied to every Insight emitted by this
    kind/role. Cartographer ships with ["overview", "cartographer"];
    custom types declare their own; everyone else gets an empty list.
    Returns a plain list (caller owns mutations).
    """
    at = _resolve_role(kind_or_role)
    if at is None or not at.auto_tag_insights:
        return []
    return list(at.auto_tag_insights)


def insight_max_length_for(kind_or_role):
    """Byte budget for an Insight emitted by this kind/role before
    truncation. Cartographer gets 8000 (repo overviews are long);
    everyone else defaults to 2000.
    """
    at = _resolve_role(kind_or_role)
    if at is None or not at.insight_max_length:
        return 2000
    return int(at.insight_max_length)


def model_routing_key_for(role):
    """The routing.rules key for this role's model + effort + runtime
    resolution. Reads AgentType.model_routing_key; defaults to
    "coding_agent" when the AgentType is missing — same as the
    legacy hardcoded value at kickoff:146 and wake:147.

    Lets a custom agent type opt into its own routing slot (e.g.
    "review_agent" for a high-effort PR reviewer that wants opus
    where coding agents settle for sonnet).
    """
    at = get_agent_type(role)
    if at is None or not at.model_routing_key:
        return "coding_agent"
    return at.model_routing_key


def kind_produces_report(kind_or_role):
    """True iff this kind/role's deliverable goes to the Report panel
    (vs the Diff panel). Reads AgentType.output_kind: "diff" means
    DiffPanel; anything else ("report", "insight") means ReportPanel.
    Returns False on unknown kinds — preserves legacy "treat as diff"
    behavior so a new kind doesn't accidentally start opening reports.
    """
    at = _resolve_role(kind_or_role)
    if at is None:
        return False
    return at.output_kind not in (None, "", "diff")


def kind_requires_scope_repo_clone(kind):
    """True iff this job kind (or task type) needs a real clone of
    scope_repo on disk. Used at memo-approve / task-spawn /
    job-execute time to fail fast when scope_repo is set but no local
    clone resolves.

    Resolves the kind through TYPE_TO_ROLE first (so "pr_review" →
    "review", "repo_analysis" → "investigation", etc.) so the same
    rule applies whether the caller is checking a task.type, an
    AgentJob.kind, or an AgentProfile.role directly.

    Returns False on any miss (unknown kind, no AgentType row,
    tombstoned default) — preserves the "fall through to scratch
    mode" behavior the legacy code defaulted to.
    """
    at = _resolve_role(kind)
    return bool(at and at.requires_scope_repo_clone)


# The four built-ins. Each entry's `protocol_md` is read from
# src/planet_maiko/prompts/<protocol_md>.md at seed time so the
# bundled .md remains the canonical source of the protocol body —
# the AgentType.protocol_prompt column is a copy refreshed on boot.
BUILT_IN_AGENT_TYPES = [
    {
        "id": "coding",
        "name": "Coder",
        "tagline": "Writes code, opens PRs",
        "description": (
            "Implements features and fixes in an isolated git worktree. "
            "Commits locally; pushes + opens a PR only after you approve."
        ),
        "icon": "code",
        "needs_worktree": True,
        "requires_scope_repo_clone": True,
        "permission_mode": None,
        "branch_prefix": "maiko",
        "supports_plan_first": True,
        "output_kind": "diff",
        "commits_locally": True,
        "produces_pr": True,
        "auto_tag_insights": [],
        "default_display_name": None,
        "model_routing_key": "coding_agent",
        "is_self_reviewing": True,
        "protocol_md": "agent-protocol",
    },
    {
        "id": "review",
        "name": "Reviewer",
        "tagline": "Reviews PRs",
        "description": (
            "Reads a PR diff, leaves inline comments, files a verdict. "
            "Read + write to the worktree; never commits or pushes."
        ),
        "icon": "git-pull-request",
        "needs_worktree": True,
        "requires_scope_repo_clone": True,
        "permission_mode": None,
        "branch_prefix": "maiko",
        "supports_plan_first": False,
        "output_kind": "diff",
        "commits_locally": False,
        "produces_pr": False,
        "auto_tag_insights": [],
        "default_display_name": None,
        "model_routing_key": "coding_agent",
        "is_self_reviewing": False,
        "protocol_md": "review-agent-protocol",
    },
    {
        "id": "investigation",
        "name": "Investigator",
        "tagline": "Digs into incidents and CI",
        "description": (
            "Investigates an incident, failing test, or unknown behavior "
            "and returns a markdown report with root cause + PATTERN / "
            "PROPOSAL blocks for the knowledge pool."
        ),
        "icon": "search",
        "needs_worktree": True,
        "permission_mode": None,
        "branch_prefix": "maiko",
        "supports_plan_first": False,
        "output_kind": "report",
        "commits_locally": False,
        "produces_pr": False,
        "auto_tag_insights": [],
        "default_display_name": None,
        "model_routing_key": "coding_agent",
        "is_self_reviewing": False,
        "protocol_md": "investigation-agent-protocol",
    },
    {
        "id": "cartographer",
        "name": "Cartographer",
        "tagline": "Maps repos into a playbook",
        "description": (
            "Walks a repo and emits an Insight with architecture, "
            "conventions, and gotchas. Read-only; runs in plan mode."
        ),
        "icon": "map",
        "needs_worktree": True,
        "permission_mode": "plan",
        "branch_prefix": "cartographer",
        "supports_plan_first": False,
        "output_kind": "insight",
        "commits_locally": False,
        "produces_pr": False,
        "auto_tag_insights": ["overview", "cartographer"],
        "insight_max_length": 8000,
        "default_display_name": "Atlas",
        "model_routing_key": "coding_agent",
        "is_self_reviewing": False,
        "protocol_md": "cartographer-agent-protocol",
    },
]


def _prompts_dir():
    """Path to the bundled <repo>/src/planet_maiko/prompts/ directory."""
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "prompts"
    )


def _load_protocol_md(name):
    """Read prompts/<name>.md. Returns None if the file is missing —
    the caller substitutes a stub so a missing prompt doesn't block
    the whole seed pass.
    """
    path = os.path.join(_prompts_dir(), f"{name}.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning(f"[agent_types] prompt file not found: {path}")
        return None
    except Exception as e:
        logger.warning(f"[agent_types] could not read {path}: {e}")
        return None


def ensure_seed_agent_types():
    """Seed the four built-in AgentType rows. Idempotent.

    For each built-in:
      - If a row with this id exists and has deleted_at set: skip
        (the user removed it; don't resurrect).
      - If a row exists and user_edited=False: refresh fields from
        BUILT_IN_AGENT_TYPES so prompt updates ship to existing
        installs. The user's customizations stay because
        user_edited=True freezes the row.
      - If no row exists: create from scratch.

    Logs a one-line summary at INFO. Best-effort: a single bad row
    doesn't block the others.
    """
    from planet_maiko.database import db
    from planet_maiko.models.agent_type import AgentType

    added = 0
    refreshed = 0
    frozen = 0
    skipped_deleted = 0

    for spec in BUILT_IN_AGENT_TYPES:
        try:
            existing = db.session.get(AgentType, spec["id"])
            protocol_md = spec.get("protocol_md")
            protocol_body = (
                _load_protocol_md(protocol_md) if protocol_md else None
            )
            if not protocol_body:
                # Stub so the row is valid; the file fallback in
                # scaffold.py will paper over the missing body until
                # the .md file is fixed.
                protocol_body = (
                    f"# {spec['name']} Protocol\n\n"
                    "Read TASK.md for instructions."
                )

            if existing is not None and existing.deleted_at is not None:
                skipped_deleted += 1
                continue

            if existing is not None and existing.user_edited:
                frozen += 1
                continue

            if existing is None:
                row = AgentType(
                    id=spec["id"],
                    name=spec["name"],
                    tagline=spec.get("tagline"),
                    description=spec.get("description"),
                    icon=spec.get("icon", "user"),
                    is_default=True,
                    is_active=True,
                    user_edited=False,
                    protocol_prompt=protocol_body,
                    needs_worktree=bool(spec.get("needs_worktree", True)),
                    requires_scope_repo_clone=bool(spec.get("requires_scope_repo_clone", False)),
                    permission_mode=spec.get("permission_mode"),
                    branch_prefix=spec.get("branch_prefix", "maiko"),
                    supports_plan_first=bool(spec.get("supports_plan_first", False)),
                    output_kind=spec.get("output_kind", "diff"),
                    commits_locally=bool(spec.get("commits_locally", False)),
                    produces_pr=bool(spec.get("produces_pr", False)),
                    auto_tag_insights=list(spec.get("auto_tag_insights") or []),
                    insight_max_length=int(spec.get("insight_max_length") or 2000),
                    default_display_name=spec.get("default_display_name"),
                    model_routing_key=spec.get("model_routing_key", "coding_agent"),
                    is_self_reviewing=bool(spec.get("is_self_reviewing", False)),
                )
                db.session.add(row)
                added += 1
            else:
                # un-edited default: refresh from the spec so prompt
                # changes flow to existing installs.
                existing.name = spec["name"]
                existing.tagline = spec.get("tagline")
                existing.description = spec.get("description")
                existing.icon = spec.get("icon", "user")
                existing.protocol_prompt = protocol_body
                existing.needs_worktree = bool(spec.get("needs_worktree", True))
                existing.requires_scope_repo_clone = bool(spec.get("requires_scope_repo_clone", False))
                existing.permission_mode = spec.get("permission_mode")
                existing.branch_prefix = spec.get("branch_prefix", "maiko")
                existing.supports_plan_first = bool(spec.get("supports_plan_first", False))
                existing.output_kind = spec.get("output_kind", "diff")
                existing.commits_locally = bool(spec.get("commits_locally", False))
                existing.produces_pr = bool(spec.get("produces_pr", False))
                existing.auto_tag_insights = list(spec.get("auto_tag_insights") or [])
                existing.insight_max_length = int(spec.get("insight_max_length") or 2000)
                existing.default_display_name = spec.get("default_display_name")
                existing.model_routing_key = spec.get("model_routing_key", "coding_agent")
                existing.is_self_reviewing = bool(spec.get("is_self_reviewing", False))
                refreshed += 1
        except Exception as e:
            logger.warning(
                f"[agent_types] failed to seed {spec.get('id')}: {e}"
            )

    if added or refreshed or frozen or skipped_deleted:
        try:
            db.session.commit()
        except Exception as e:
            logger.warning(f"[agent_types] commit failed: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass
            return
        logger.info(
            f"[agent_types] seed pass: added={added}, refreshed={refreshed}, "
            f"frozen={frozen}, skipped_deleted={skipped_deleted}"
        )


# IDs of the four built-in protocol-template CustomSkill rows. These
# are NOT copied into Specialty during backfill — they were always
# protocols, not specialties, and AgentType now owns that concept.
_PROTOCOL_SKILL_IDS = {
    "agent-protocol",
    "review-agent-protocol",
    "investigation-agent-protocol",
    "cartographer-agent-protocol",
}


def backfill_from_custom_skills():
    """One-shot: copy CustomSkill rows into Specialty / AgentType.

    Migration shape:
      - CustomSkill.id in _PROTOCOL_SKILL_IDS → skip. AgentType
        already owns these via ensure_seed_agent_types.
      - CustomSkill with non-empty protocol_prompt (the post-issue-#22
        knob) → migrate to AgentType as a custom type with
        is_default=False.
      - Everything else → migrate to Specialty.

    Idempotent: targets are matched by id; existing rows are left
    alone. Safe to run on every boot.

    Logs counts at INFO. Best-effort: a single bad row doesn't block
    the rest.
    """
    from planet_maiko.database import db
    from planet_maiko.models.agent_type import AgentType
    from planet_maiko.models.custom_skill import CustomSkill
    from planet_maiko.models.specialty import Specialty

    to_specialty = 0
    to_agent_type = 0
    skipped_protocol = 0
    skipped_existing = 0
    failed = 0

    try:
        skills = CustomSkill.query.all()
    except Exception as e:
        logger.debug(f"[agent_types] backfill skipped (no CustomSkill table?): {e}")
        return

    for skill in skills:
        try:
            if skill.id in _PROTOCOL_SKILL_IDS:
                skipped_protocol += 1
                continue

            if skill.protocol_prompt:
                # Custom agent type — created via /api/skills with the
                # post-#22 knob set. Move to AgentType.
                if db.session.get(AgentType, skill.id) is not None:
                    skipped_existing += 1
                    continue
                db.session.add(AgentType(
                    id=skill.id,
                    name=skill.name,
                    tagline=skill.description,
                    description=skill.description,
                    icon=skill.icon or "user",
                    is_default=False,
                    is_active=True,
                    user_edited=bool(skill.user_edited),
                    deleted_at=skill.deleted_at,
                    protocol_prompt=skill.protocol_prompt,
                    needs_worktree=bool(skill.needs_worktree),
                    permission_mode=skill.permission_mode,
                    branch_prefix="maiko",
                    supports_plan_first=False,
                    output_kind="diff",
                    commits_locally=False,
                    produces_pr=False,
                    auto_tag_insights=[],
                    default_display_name=None,
                    model_routing_key="coding_agent",
                    is_self_reviewing=False,
                ))
                to_agent_type += 1
            else:
                # Plain specialty — the historical case.
                if db.session.get(Specialty, skill.id) is not None:
                    skipped_existing += 1
                    continue
                db.session.add(Specialty(
                    id=skill.id,
                    name=skill.name,
                    description=skill.description,
                    prompt=skill.prompt,
                    mcps=list(skill.mcps or []),
                    icon=skill.icon or "wand",
                    is_default=bool(skill.is_default),
                    user_edited=bool(skill.user_edited),
                    deleted_at=skill.deleted_at,
                    last_run_at=skill.last_run_at,
                ))
                to_specialty += 1
        except Exception as e:
            failed += 1
            logger.warning(
                f"[agent_types] backfill failed for {skill.id}: {e}"
            )

    if to_specialty or to_agent_type or failed:
        try:
            db.session.commit()
        except Exception as e:
            logger.warning(f"[agent_types] backfill commit failed: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass
            return
        logger.info(
            f"[agent_types] CustomSkill backfill: "
            f"specialties={to_specialty}, agent_types={to_agent_type}, "
            f"skipped_protocol={skipped_protocol}, "
            f"skipped_existing={skipped_existing}, failed={failed}"
        )
