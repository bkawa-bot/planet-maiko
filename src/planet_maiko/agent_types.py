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
    """Tags applied verbatim to every Insight emitted by this kind/role.

    Cartographer outputs auto-route into the ## Repo Overview block
    via the "overview" + "cartographer" tags. Everyone else gets an
    empty list. Was previously a per-AgentType column; collapsed to
    this one hardcoded special case after the pass 2 trim showed
    no other type ever wanted it.
    """
    try:
        from planet_maiko.orchestration import TYPE_TO_ROLE
    except Exception:
        TYPE_TO_ROLE = {}
    role = TYPE_TO_ROLE.get(kind_or_role, kind_or_role)
    if role in ("cartograph", "cartographer"):
        return ["overview", "cartographer"]
    return []


# Single byte budget for any Insight emitted by an agent before
# truncation. Was per-type (cartographer 8000, others 2000); the
# 2000 cap was arbitrary and nothing else fills 8000 anyway. Held
# as a constant rather than a column so a new type doesn't have
# to know to set it.
_INSIGHT_MAX_LENGTH = 8000


def insight_max_length_for(kind_or_role):
    """Byte budget for an Insight emitted by this kind/role before
    truncation. Constant across all types now (was a per-AgentType
    column before pass 2).
    """
    return _INSIGHT_MAX_LENGTH


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

    Implementation reads AgentType.spawn_mode (was the
    requires_scope_repo_clone boolean before pass 2): "worktree"
    means a real clone is required, "scratch" means the agent
    happily falls through to a scratch dir.

    Returns False on any miss (unknown kind, no AgentType row,
    tombstoned default) — preserves the "fall through to scratch
    mode" behavior the legacy code defaulted to.
    """
    at = _resolve_role(kind)
    return bool(at and at.spawn_mode == "worktree")


# The four built-ins. Each entry's `protocol_md` is read from
# src/planet_maiko/prompts/<protocol_md>.md at seed time so the
# bundled .md remains the canonical source of the protocol body —
# the AgentType.protocol_prompt column is a copy refreshed on boot.
BUILT_IN_AGENT_TYPES = [
    {
        "id": "coding",
        "name": "Coder",
        "description": (
            "Implements features and fixes in an isolated git worktree. "
            "Commits locally; pushes + opens a PR only after you approve."
        ),
        "icon": "code",
        "spawn_mode": "worktree",
        "permission_mode": None,
        "output_kind": "diff",
        "input_kind": "task",
        "accepts": ["task", "plan", "report"],
        "model_routing_key": "coding_agent",
        "protocol_md": "agent-protocol",
    },
    {
        "id": "review",
        "name": "Reviewer",
        "description": (
            "Reads a PR diff, leaves inline comments, files a verdict. "
            "Read + write to the worktree; never commits or pushes."
        ),
        "icon": "git-pull-request",
        "spawn_mode": "worktree",
        "permission_mode": None,
        "output_kind": "diff",
        "input_kind": "diff",
        "accepts": ["diff"],
        "model_routing_key": "coding_agent",
        "protocol_md": "review-agent-protocol",
    },
    {
        "id": "investigation",
        "name": "Investigator",
        "description": (
            "Investigates an incident, failing test, or unknown behavior "
            "and returns a markdown report with root cause + PATTERN / "
            "PROPOSAL blocks for the knowledge pool."
        ),
        "icon": "search",
        "spawn_mode": "scratch",
        "permission_mode": None,
        "output_kind": "report",
        "input_kind": "incident",
        "accepts": ["incident", "task"],
        "model_routing_key": "coding_agent",
        "protocol_md": "investigation-agent-protocol",
    },
    {
        "id": "cartographer",
        "name": "Cartographer",
        "description": (
            "Walks a repo and emits an Insight with architecture, "
            "conventions, and gotchas. Read-only; runs in plan mode."
        ),
        "icon": "map",
        "spawn_mode": "scratch",
        "permission_mode": "plan",
        "output_kind": "insight",
        "input_kind": "repo",
        "accepts": ["repo"],
        "model_routing_key": "coding_agent",
        "protocol_md": "cartographer-agent-protocol",
    },
    {
        "id": "planner",
        "name": "Planner",
        "description": (
            "Reads the task and the repo, then writes an implementation "
            "plan (steps, files, risks) for a coder to follow. Read-only: "
            "it proposes, it does not build."
        ),
        "icon": "clipboard",
        "spawn_mode": "worktree",
        "permission_mode": "plan",
        "output_kind": "plan",
        "input_kind": "task",
        "accepts": ["task"],
        "model_routing_key": "coding_agent",
        "protocol_md": "planner-agent-protocol",
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
                    description=spec.get("description"),
                    icon=spec.get("icon", "user"),
                    is_default=True,
                    user_edited=False,
                    protocol_prompt=protocol_body,
                    spawn_mode=spec.get("spawn_mode", "worktree"),
                    permission_mode=spec.get("permission_mode"),
                    output_kind=spec.get("output_kind", "diff"),
                    input_kind=spec.get("input_kind", "task"),
                    accepts=spec.get("accepts") or [spec.get("input_kind", "task")],
                    model_routing_key=spec.get("model_routing_key", "coding_agent"),
                )
                db.session.add(row)
                added += 1
            else:
                # un-edited default: refresh from the spec so prompt
                # changes flow to existing installs.
                existing.name = spec["name"]
                existing.description = spec.get("description")
                existing.icon = spec.get("icon", "user")
                existing.protocol_prompt = protocol_body
                existing.spawn_mode = spec.get("spawn_mode", "worktree")
                existing.permission_mode = spec.get("permission_mode")
                existing.output_kind = spec.get("output_kind", "diff")
                existing.input_kind = spec.get("input_kind", "task")
                existing.accepts = spec.get("accepts") or [spec.get("input_kind", "task")]
                existing.model_routing_key = spec.get("model_routing_key", "coding_agent")
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
                    description=skill.description,
                    icon=skill.icon or "user",
                    is_default=False,
                    user_edited=bool(skill.user_edited),
                    deleted_at=skill.deleted_at,
                    protocol_prompt=skill.protocol_prompt,
                    # CustomSkill.needs_worktree maps to worktree when
                    # True (real clone expected), scratch otherwise.
                    spawn_mode="worktree" if bool(skill.needs_worktree) else "scratch",
                    permission_mode=skill.permission_mode,
                    output_kind="diff",
                    model_routing_key="coding_agent",
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
