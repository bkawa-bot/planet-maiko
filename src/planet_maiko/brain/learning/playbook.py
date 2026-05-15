"""Repo Overview + Team Playbook rendering from active Insights.

Builds the shared-per-repo Markdown block that every new coding agent
gets injected into CLAUDE.md at worktree prep time, and the same
structured insight list via the read-surface HTTP endpoint so external
orchestrators (Phase B) can feed their own agents the same context.

Distinct from Learnings: Insights are tribal / operational notes, no
LoRA training, injected verbatim.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def build_playbook(parent_repo_path):
    """Render the Repo Overview + Team Playbook sections from active
    Insights scoped to this repo (or global).

    Insights tagged "overview" are cold-start context — the full
    architecture / conventions / gotchas map. They render verbatim
    as an H2 so the agent sees them before anything else. All other
    insights render as the usual bullet list.

    Best-effort: DB errors, no matching repo, or empty insight set
    all return empty playbook_md + empty insights list so callers can
    gracefully skip rendering a section.

    Args:
        parent_repo_path: either an absolute path to a local clone or
            a bare "org/repo" / "repo" string. Only the last path
            segment is used for matching, so both forms work.

    Returns:
        dict with:
            playbook_md: rendered markdown block (empty string when
                no insights match). Same output the agent-prep path
                writes into CLAUDE.md today.
            insights: structured list of Insight.to_dict() payloads,
                ordered the same way they render in the markdown
                (overviews first, then bullet insights). Empty list
                when playbook_md is empty.
    """
    try:
        from planet_maiko.models.insight import Insight
        # Extract "org/repo" or just "repo" from the parent path if we
        # can — insights are keyed by the repo name the user stored.
        # Fall back to matching any globals.
        repo_key = None
        if parent_repo_path:
            repo_key = os.path.basename(parent_repo_path.rstrip(os.sep))

        q = Insight.query.filter(Insight.status == "active")
        if repo_key:
            from sqlalchemy import or_
            q = q.filter(
                or_(
                    Insight.repo_scope == repo_key,
                    Insight.repo_scope.is_(None),
                    Insight.repo_scope.like(f"%/{repo_key}"),
                )
            )
        else:
            q = q.filter(Insight.repo_scope.is_(None))

        insights = q.order_by(Insight.last_confirmed_at.desc()).limit(40).all()
        now = datetime.now(timezone.utc)
        fresh = [i for i in insights if not i.is_expired(now)]
        if not fresh:
            return {"playbook_md": "", "insights": []}

        overviews = [i for i in fresh if i.tags and "overview" in i.tags]
        bullets = [i for i in fresh if not (i.tags and "overview" in i.tags)]

        parts = []
        if overviews:
            parts.append("## Repo Overview")
            parts.append("")
            parts.append(
                "Cold-start map for this repo — architecture, conventions, "
                "gotchas, and what NOT to do. Read first so you don't "
                "rediscover what another agent already mapped."
            )
            parts.append("")
            for ins in overviews:
                parts.append(ins.text.strip())
                parts.append("")
        if bullets:
            parts.append("## Team Playbook")
            parts.append("")
            parts.append(
                "Tribal knowledge the pack has captured about this repo "
                "and how to work in it. Read before starting — saves you "
                "from re-discovering things another agent already figured out."
            )
            parts.append("")
            for ins in bullets:
                tag_str = ""
                if ins.tags:
                    tag_str = " _" + ", ".join(ins.tags) + "_"
                parts.append(f"- {ins.text.strip()}{tag_str}")

        ordered = overviews + bullets
        return {
            "playbook_md": "\n".join(parts).rstrip(),
            "insights": [ins.to_dict() for ins in ordered],
        }
    except Exception as e:
        logger.debug(f"[playbook] build skipped: {e}")
        return {"playbook_md": "", "insights": []}
