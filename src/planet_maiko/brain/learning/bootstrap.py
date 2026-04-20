"""Bootstrap learning engine from historical PR reviews."""

import logging
import subprocess
import json
import threading
from datetime import datetime, timezone
from planet_maiko.database import db
from planet_maiko.models.signal import Signal
from planet_maiko.config import load_config

logger = logging.getLogger(__name__)

# Pre-LLM filters have been removed — the synthesis step is better at
# separating signal from noise than brittle length / phrase heuristics
# were. Only empty bodies are skipped here.


def fetch_comments_for_pr(repo, pr_number, timeout=60):
    """Fetch every inline review comment on a single PR.

    Used by the "scrape on PR merge" flow: we don't need the full
    repo-wide batch, just the comments on the one PR that just merged.
    Same shape as _fetch_inline_review_comments — a flat list of dicts
    with body, author, path, line, diff_hunk.
    """
    try:
        result = subprocess.run(
            ["gh", "api", "--paginate",
             f"repos/{repo}/pulls/{pr_number}/comments?per_page=100"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning(
                f"[bootstrap] Per-PR comment fetch failed for {repo}#{pr_number}: "
                f"{result.stderr.strip()[:160]}"
            )
            return []
        raw = result.stdout.strip()
        if not raw:
            return []
        try:
            comments = json.loads(raw)
        except json.JSONDecodeError:
            comments = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    if isinstance(chunk, list):
                        comments.extend(chunk)
                    elif isinstance(chunk, dict):
                        comments.append(chunk)
                except json.JSONDecodeError:
                    continue
    except subprocess.TimeoutExpired:
        logger.warning(f"[bootstrap] Timeout on {repo}#{pr_number}")
        return []
    except Exception as e:
        logger.warning(f"[bootstrap] Per-PR comment error {repo}#{pr_number}: {e}")
        return []

    out = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        body = (c.get("body") or "").strip()
        if not body:
            continue
        cid = c.get("id")
        out.append({
            # Stringified so the DB column (VARCHAR) matches whatever
            # GitHub emits (integer today, historically).
            "id": str(cid) if cid is not None else None,
            "body": body,
            "author": (c.get("user") or {}).get("login", ""),
            "path": c.get("path"),
            "line": c.get("line"),
            "diff_hunk": c.get("diff_hunk"),
            "pr_number": pr_number,
        })
    return out


def _fetch_inline_review_comments(repo, timeout=120):
    """Fetch every inline (per-file, per-line) PR review comment in a repo.

    Uses the repo-level REST endpoint (/repos/{owner}/{repo}/pulls/comments)
    which returns all inline review comments across all PRs, paginated.
    That's one gh api --paginate call per repo regardless of PR count.

    Review summary bodies ("LGTM", "Just one small nit") and PR
    conversation comments are intentionally skipped — they're mostly
    praise / clarifying questions and don't pair with code for training.
    Only inline comments carry the diff_hunk that makes signals useful.

    Returns a flat list of comment-like dicts with body, author, path,
    line, diff_hunk, pr_number. Best-effort: on failure returns [].
    """
    try:
        # Newest comments first — when the user caps `limit` we want
        # the most recent N, not the oldest N from ancient history.
        result = subprocess.run(
            ["gh", "api", "--paginate",
             f"repos/{repo}/pulls/comments?per_page=100&sort=created&direction=desc"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning(f"[bootstrap] Inline comments fetch failed for {repo}: {result.stderr.strip()[:160]}")
            return []
        raw = result.stdout.strip()
        if not raw:
            return []
        # gh api --paginate on array endpoints returns a single merged
        # JSON array. For safety fall back to line-by-line parsing if
        # that assumption changes upstream.
        try:
            comments = json.loads(raw)
        except json.JSONDecodeError:
            comments = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    if isinstance(chunk, list):
                        comments.extend(chunk)
                    elif isinstance(chunk, dict):
                        comments.append(chunk)
                except json.JSONDecodeError:
                    continue
    except subprocess.TimeoutExpired:
        logger.warning(f"[bootstrap] Inline comments timeout for {repo}")
        return []
    except Exception as e:
        logger.warning(f"[bootstrap] Inline comments error for {repo}: {e}")
        return []

    out = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        body = (c.get("body") or "").strip()
        if not body:
            continue
        pr_url = c.get("pull_request_url") or ""
        try:
            pr_number = int(pr_url.rsplit("/", 1)[-1]) if pr_url else None
        except (ValueError, AttributeError):
            pr_number = None
        cid = c.get("id")
        out.append({
            "id": str(cid) if cid is not None else None,
            "body": body,
            "author": (c.get("user") or {}).get("login", ""),
            "path": c.get("path"),
            "line": c.get("line"),
            # diff_hunk is the code context — a few lines of diff around
            # where the reviewer left the comment. Gold for LoRA training:
            # the model sees both the code and the human's reaction to it.
            "diff_hunk": c.get("diff_hunk"),
            "pr_number": pr_number,
        })
    return out


# Progress state for the async backfill job. Accessed through the helpers
# below so we have a single place to update it and a single lock guarding
# the dict. The UI polls get_backfill_progress() to render a progress bar.
_progress_lock = threading.Lock()
_progress = {
    "running": False,
    "phase": "idle",          # idle | fetching | synthesizing | aggregating | done | error
    "repos_total": 0,
    "repos_done": 0,
    "current_repo": None,
    "comments_total": 0,      # inline comments found in the current repo
    "comments_done": 0,       # how many we've processed so far
    "signals_created": 0,     # running count of new signals across all repos
    "synthesized": 0,
    "new_learnings": 0,
    "graduated": 0,
    "learnings_merged": 0,    # how many duplicate learnings the clustering pass merged
    "error": None,
    "started_at": None,
    "finished_at": None,
    "result": None,           # full result dict once phase == done
}


def get_backfill_progress():
    with _progress_lock:
        return dict(_progress)


def update_backfill_progress(**kwargs):
    with _progress_lock:
        _progress.update(kwargs)


def reset_backfill_progress():
    with _progress_lock:
        _progress.update({
            "running": False, "phase": "idle",
            "repos_total": 0, "repos_done": 0,
            "current_repo": None, "comments_total": 0, "comments_done": 0,
            "signals_created": 0, "synthesized": 0, "new_learnings": 0,
            "graduated": 0, "learnings_merged": 0, "error": None,
            "started_at": None, "finished_at": None, "result": None,
        })


def bootstrap_from_prs(limit=None, repos=None):
    """Scan a repo's inline PR review comments and store them as signals.

    Only inline (per-file, per-line) comments are fetched — review
    summary bodies ("LGTM") and PR conversation comments are intentionally
    skipped. Inline comments are where the real pattern feedback lives
    and they come with a diff_hunk we persist as the signal's
    code_context (directly usable for LoRA training).

    Args:
        limit: optional cap on inline comments per repo. None = all.
        repos: list of repos to scan (None = all configured repos).

    Returns:
        dict with total_created and per_repo stats.
    """
    if repos is None:
        config = load_config()
        repos = config.get("github", {}).get("repos", [])

    if not repos:
        logger.warning("[bootstrap] No repos configured or specified")
        return {"total_created": 0, "per_repo": []}

    total_created = 0
    per_repo = []
    update_backfill_progress(repos_total=len(repos), repos_done=0)

    for repo in repos:
        update_backfill_progress(
            current_repo=repo, comments_total=0, comments_done=0,
        )
        repo_stats = {"repo": repo, "comments_scanned": 0, "signals_created": 0, "error": None}

        try:
            # One paginated API call gives us every inline comment in
            # the repo (across all PRs, open or closed).
            inline = _fetch_inline_review_comments(repo)
            if limit is not None and limit > 0 and len(inline) > limit:
                inline = inline[:limit]

            repo_stats["comments_scanned"] = len(inline)
            update_backfill_progress(comments_total=len(inline))

            # Preload existing signals for this repo keyed by text so we
            # can merge new occurrences onto the same signal row
            # (one Signal per unique comment, many examples per signal).
            preload = {
                s.text: s
                for s in Signal.query.filter_by(
                    repo=repo, source_type="pr_comment"
                ).all()
            }

            created_for_repo = 0
            examples_added = 0
            for i, entry in enumerate(inline, start=1):
                body = entry["body"][:500]
                example = {
                    "path": entry.get("path") or None,
                    "diff_hunk": entry.get("diff_hunk") or None,
                    "author": entry.get("author", "") or "",
                    "line": entry.get("line"),
                }
                ex_key = (example["path"] or "", example["diff_hunk"] or "")

                existing = preload.get(body)
                if existing:
                    # Same comment — append this occurrence to the
                    # signal's example list unless it's already there.
                    current = list(existing.examples or [])
                    already = any(
                        ((e.get("path") or ""), (e.get("diff_hunk") or "")) == ex_key
                        for e in current
                    )
                    if not already:
                        current.append(example)
                        existing.examples = current
                        examples_added += 1
                else:
                    signal = Signal(
                        category="pattern",
                        text=body,
                        source_type="pr_comment",
                        reviewer=example["author"],
                        severity="suggestion",
                        repo=repo,
                        file_path=example["path"],
                        code_context=example["diff_hunk"],
                        examples=[example],
                    )
                    db.session.add(signal)
                    preload[body] = signal
                    created_for_repo += 1

                # Emit live progress so the UI actually moves. Every 25
                # comments is enough — polling runs every 1.5s and this
                # keeps per-write overhead low.
                if i % 25 == 0 or i == len(inline):
                    update_backfill_progress(
                        comments_done=i,
                        signals_created=total_created + created_for_repo,
                    )

            if examples_added:
                logger.info(f"[bootstrap] {repo}: {created_for_repo} new signals + {examples_added} examples appended to existing")

            repo_stats["signals_created"] = created_for_repo
            total_created += created_for_repo

        except Exception as e:
            repo_stats["error"] = str(e)[:200]
            logger.warning(f"[bootstrap] Error scanning {repo}: {e}")

        per_repo.append(repo_stats)
        update_backfill_progress(
            repos_done=len(per_repo),
            signals_created=total_created,
        )

    if total_created:
        db.session.commit()
        logger.info(f"[bootstrap] Created {total_created} signals from {len(repos)} repo(s)")

    return {"total_created": total_created, "per_repo": per_repo}
