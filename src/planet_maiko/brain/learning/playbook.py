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


def _matches_context(insight, ctx_set):
    """True iff the insight is relevant to the given job/agent context.

    Match rules:
      - Untagged insights pass — they're general repo context, no
        scope claim attached.
      - The "overview" tag always passes — that's the cartographer's
        cold-start repo map, not a context-scoped note.
      - At least one tag overlaps the context set (case-insensitive).

    ctx_set is the already-lowercased context tag set. The caller
    builds it from job kind / role / task tags / project / specialty
    so each insight only surfaces in jobs where its scope makes sense.
    """
    tags = insight.tags or []
    if not tags:
        return True
    tag_lower = {str(t).lower() for t in tags if t}
    if "overview" in tag_lower:
        return True
    return bool(tag_lower & ctx_set)


def build_playbook(parent_repo_path, *, context_tags=None):
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
        context_tags: optional iterable of strings describing the
            job/agent context (role, kind, task tags, project name,
            specialty id, etc.). When provided, the result is filtered
            by tag relevance — see _matches_context for the rule. None
            means no relevance filter (every active insight matching
            the repo scope is returned, the legacy behavior). The
            read-surface HTTP endpoint passes None so the user-facing
            playbook page shows the full repo set; agent injection at
            worktree-prep time passes a populated set so the agent
            doesn't have to skim 40 unrelated bullets.

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

        # Pull a wider candidate set when context-filtering — the
        # filter shrinks the set significantly, so a hard 40-cap on
        # the query would starve the post-filter result. Cap at 120
        # candidates pre-filter when context_tags is set; otherwise
        # keep the original 40-cap (the legacy "show everything"
        # behavior).
        cap = 120 if context_tags else 40
        insights = q.order_by(Insight.last_confirmed_at.desc()).limit(cap).all()
        now = datetime.now(timezone.utc)
        fresh = [i for i in insights if not i.is_expired(now)]
        if not fresh:
            return {"playbook_md": "", "insights": []}

        # Tag-relevance filter. None = no filter (existing callers,
        # /playbook HTTP endpoint). A set = filter to insights whose
        # tags overlap context_tags OR are untagged OR are an
        # "overview" insight (always-show cold-start map).
        ctx_set = None
        if context_tags is not None:
            ctx_set = {str(t).lower() for t in context_tags if t}
            fresh = [i for i in fresh if _matches_context(i, ctx_set)]
            # Trim back to the legacy display cap after filtering so
            # the rendered block doesn't grow if a job's context tags
            # are very broad.
            fresh = fresh[:40]
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
