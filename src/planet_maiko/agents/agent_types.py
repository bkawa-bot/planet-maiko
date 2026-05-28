"""Built-in AgentType seeds + idempotent seeding helper.

PR 1 of the AgentType refactor (issue #22). Defines the six existing
type slugs the codebase has historically dispatched on and seeds them
into the agent_types table on every boot so:

  * Existing AgentJob.kind / Task.type strings resolve to a real row.
  * New custom types created via the API later get a consistent
    schema to extend.

The values here mirror current behavior — they're a snapshot of what
the hardcoded sets (WORKTREE_REQUIRED_KINDS, ONE_SHOT_ROLE_FOR_TYPE,
the cartographer permission_mode override in kickoff.py) imply today.
PR 2+ will refactor the call sites to read these values; PR 1 just
makes them queryable.

`pr_review` and `repo_analysis` are seeded as their own types for
this PR so existing data keeps working. The collapse into `review`
and `investigation` respectively happens in a later PR with a proper
data migration on AgentJob.kind / Task.type values.
"""

import logging

logger = logging.getLogger(__name__)


# Spec dicts. Each entry's key becomes the AgentType.id slug. Fields
# match the model 1:1 except for is_builtin (always True for seeds)
# and timestamps (defaults). Add new builtins by adding entries here;
# the seeder is idempotent so existing rows get updated to match.
BUILTIN_AGENT_TYPES = {
    "coding": {
        "display_name": "Coding",
        "description": (
            "Writes code changes on a branch. Hands the diff back for "
            "the user to review and merge."
        ),
        "spawn_mode": "worktree",
        "output_contract": "diff",
        "permission_mode": None,
        "expected_inputs": ["repo_path"],
        "allowed_tools": [],
    },
    "review": {
        "display_name": "Review",
        "description": (
            "Reviews a set of changes and leaves inline comments plus "
            "a verdict. Doesn't write code itself."
        ),
        "spawn_mode": "worktree",
        "output_contract": "comment",
        "permission_mode": None,
        "expected_inputs": [],
        "allowed_tools": [],
    },
    # Folds into 'review' in a later PR. Kept as a distinct row for PR 1
    # so existing AgentJob.kind = "pr_review" data still resolves.
    "pr_review": {
        "display_name": "PR review",
        "description": (
            "Reviews a pull request someone else opened. Equivalent to "
            "'review' but with the PR URL on the task."
        ),
        "spawn_mode": "worktree",
        "output_contract": "comment",
        "permission_mode": None,
        "expected_inputs": ["pr_url"],
        "allowed_tools": [],
    },
    "investigation": {
        "display_name": "Investigation",
        "description": (
            "Traces through incidents, error spikes, or repo questions "
            "and produces a written report."
        ),
        "spawn_mode": "scratch",
        "output_contract": "report",
        "permission_mode": None,
        "expected_inputs": [],
        "allowed_tools": [],
    },
    # Folds into 'investigation' in a later PR. Same rationale as
    # pr_review — kept distinct now so existing rows resolve.
    "repo_analysis": {
        "display_name": "Repo analysis",
        "description": (
            "Read-only investigation flavor. Same shape as "
            "'investigation' with restricted permissions."
        ),
        "spawn_mode": "scratch",
        "output_contract": "report",
        "permission_mode": "plan",
        "expected_inputs": [],
        "allowed_tools": [],
    },
    "cartograph": {
        "display_name": "Cartograph",
        "description": (
            "Maps an unfamiliar repo into a navigable overview so "
            "future agents know where things live."
        ),
        "spawn_mode": "scratch",
        "output_contract": "report",
        "permission_mode": "plan",
        "expected_inputs": [],
        "allowed_tools": [],
    },
}


# Fields that the seeder will overwrite on existing builtin rows when
# the spec changes. display_name / description stay user-editable
# (the UI will let users localize labels), so they're only set on
# first insert, not on re-sync.
_RESYNC_FIELDS = (
    "spawn_mode",
    "output_contract",
    "permission_mode",
    "allowed_tools",
    "expected_inputs",
)


def seed_default_types():
    """Idempotent seed of BUILTIN_AGENT_TYPES into agent_types.

    Run on every app boot after db.create_all (same shape as
    seed_defaults() for CustomSkill). On first run: inserts all
    builtins. On subsequent runs:
      * inserts any builtins added since last boot,
      * resyncs structural fields (spawn_mode, output_contract, etc.)
        on builtin rows so behavior changes in this file propagate,
      * leaves user-edited fields (display_name, description) alone,
      * respects soft-delete tombstones — a builtin the user removed
        does not resurrect.
    """
    from planet_maiko.database import db
    from planet_maiko.models.agent_type import AgentType

    inserted = 0
    updated = 0
    for type_id, spec in BUILTIN_AGENT_TYPES.items():
        row = db.session.get(AgentType, type_id)
        if row is None:
            row = AgentType(
                id=type_id,
                display_name=spec["display_name"],
                description=spec["description"],
                spawn_mode=spec["spawn_mode"],
                output_contract=spec["output_contract"],
                permission_mode=spec.get("permission_mode"),
                allowed_tools=spec.get("allowed_tools", []),
                expected_inputs=spec.get("expected_inputs", []),
                protocol=None,  # filled in by editor UI when user wants to override
                is_builtin=True,
            )
            db.session.add(row)
            inserted += 1
            continue

        if row.deleted_at is not None:
            # User soft-deleted this builtin; don't resurrect or sync.
            continue

        # Resync structural fields so changes here land on existing
        # installs. display_name + description stay as-is (treated
        # like user-editable copy).
        changed = False
        for field in _RESYNC_FIELDS:
            target = spec.get(field) if field in ("permission_mode",) else spec.get(field, [])
            current = getattr(row, field)
            if current != target:
                setattr(row, field, target)
                changed = True
        if changed:
            updated += 1

    if inserted or updated:
        db.session.commit()
        logger.info(
            f"[agent_types] seeded {inserted} new, resynced {updated} "
            f"existing builtin type(s)"
        )
