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


def _fetch_inline_review_comments(repo, timeout=120):
    """Fetch every inline (per-file, per-line) PR review comment in a repo.

    Uses the repo-level REST endpoint (/repos/{owner}/{repo}/pulls/comments)
    which returns all inline review comments across all PRs, paginated.
    That's one gh api call per repo regardless of PR count — the per-PR
    alternative would take ~309 calls for a busy repo.

    Returns a dict mapping PR number → list of comment-like dicts with
    body, author, path, line. Best-effort: on failure returns {}.
    """
    try:
        result = subprocess.run(
            ["gh", "api", "--paginate",
             f"repos/{repo}/pulls/comments?per_page=100"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning(f"[bootstrap] Inline comments fetch failed for {repo}: {result.stderr.strip()[:160]}")
            return {}
        raw = result.stdout.strip()
        if not raw:
            return {}
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
        return {}
    except Exception as e:
        logger.warning(f"[bootstrap] Inline comments error for {repo}: {e}")
        return {}

    by_pr = {}
    for c in comments:
        if not isinstance(c, dict):
            continue
        pr_url = c.get("pull_request_url") or ""
        if not pr_url:
            continue
        try:
            pr_number = int(pr_url.rsplit("/", 1)[-1])
        except (ValueError, AttributeError):
            continue
        body = (c.get("body") or "").strip()
        if not body:
            continue
        by_pr.setdefault(pr_number, []).append({
            "body": body,
            "author": {"login": (c.get("user") or {}).get("login", "")},
            "path": c.get("path"),
            "line": c.get("line"),
            # diff_hunk is the code context — a few lines of diff around
            # where the reviewer left the comment. Gold for LoRA training:
            # the model sees both the code and the human's reaction to it.
            "diff_hunk": c.get("diff_hunk"),
        })
    return by_pr


# Progress state for the async backfill job. Accessed through the helpers
# below so we have a single place to update it and a single lock guarding
# the dict. The UI polls get_backfill_progress() to render a progress bar.
_progress_lock = threading.Lock()
_progress = {
    "running": False,
    "phase": "idle",          # idle | fetching | synthesizing | aggregating | done | error
    "stage": None,            # within "fetching": listing | inline | processing
    "repos_total": 0,
    "repos_done": 0,
    "current_repo": None,
    "prs_in_repo": 0,         # total PRs fetched for the current repo
    "prs_done": 0,            # how many of those we've processed so far
    "signals_created": 0,     # running count across all repos (updated per-PR)
    "synthesized": 0,
    "new_learnings": 0,
    "graduated": 0,
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
            "running": False, "phase": "idle", "stage": None,
            "repos_total": 0, "repos_done": 0,
            "current_repo": None, "prs_in_repo": 0, "prs_done": 0,
            "signals_created": 0, "synthesized": 0, "new_learnings": 0,
            "graduated": 0, "error": None,
            "started_at": None, "finished_at": None, "result": None,
        })


def bootstrap_from_prs(limit=20, repos=None):
    """Scan recent merged PRs and extract review comments as signals.

    Uses gh CLI to fetch PR review comments. Each comment becomes a signal
    with 0.5x confidence weight (historical, not live).

    Args:
        limit: max PRs to scan per repo
        repos: list of repos to scan (None = all configured repos)

    Returns:
        dict with: total_created, per_repo (list of {repo, prs_scanned,
                   signals_created, error})
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
        update_backfill_progress(current_repo=repo, prs_in_repo=0, prs_done=0, stage="listing")
        repo_stats = {"repo": repo, "prs_scanned": 0, "signals_created": 0, "error": None}

        try:
            # We fetch both `reviews` (top-level review summary bodies) and
            # `comments` (PR conversation comments) because most real
            # feedback lives in the conversation, not the review summary —
            # bare approvals + inline comments are common patterns and
            # `reviews` alone captures almost none of that.
            # (Inline per-file review comments require a separate per-PR
            # API call and aren't pulled here yet — follow-up.)
            result = subprocess.run(
                ["gh", "pr", "list", "--repo", repo, "--state", "merged",
                 "--limit", str(limit), "--json", "number,title,reviews,comments"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                err = result.stderr.strip()[:200]
                repo_stats["error"] = err or f"gh exited {result.returncode}"
                logger.warning(f"[bootstrap] Failed to list PRs for {repo}: {err}")
                per_repo.append(repo_stats)
                continue

            prs = json.loads(result.stdout)
            repo_stats["prs_scanned"] = len(prs)
            update_backfill_progress(prs_in_repo=len(prs), stage="inline")

            # Batch-fetch every inline review comment in the repo (single
            # API call, regardless of PR count). Indexed by PR number so
            # we can merge them into each PR's entry stream below.
            inline_by_pr = _fetch_inline_review_comments(repo)

            update_backfill_progress(stage="processing")
            created_for_repo = 0

            for pr_index, pr in enumerate(prs, start=1):
                # Three kinds of feedback on a PR, all just "someone
                # wrote something" to the backfill:
                #   1. review summary bodies (the "Files changed" Review box)
                #   2. conversation comments (the PR discussion tab)
                #   3. inline code review comments (per-file, per-line)
                # Duplicates get filtered below by text+repo.
                entries = []
                for review in (pr.get("reviews") or []):
                    entries.append(review)
                for comment in (pr.get("comments") or []):
                    entries.append(comment)
                pr_number = pr.get("number")
                if pr_number is not None:
                    for inline in inline_by_pr.get(pr_number, []):
                        entries.append(inline)

                for entry in entries:
                    body = (entry.get("body") or "").strip()
                    if not body:
                        continue

                    # Inline comments carry the diff hunk + file path;
                    # review bodies and conversation comments don't.
                    # When present, these fields turn the signal into
                    # gold LoRA training data (code + human reaction).
                    diff_hunk = entry.get("diff_hunk") or None
                    file_path = entry.get("path") or None

                    existing = Signal.query.filter_by(
                        text=body[:500], repo=repo, source_type="pr_comment"
                    ).first()
                    if existing:
                        # Upgrade older signals in place: if we have a
                        # diff hunk this time and the existing row has
                        # none, backfill it so training can pick it up.
                        # Otherwise skip as before.
                        if diff_hunk and not existing.code_context:
                            existing.code_context = diff_hunk
                            if file_path and not existing.file_path:
                                existing.file_path = file_path
                        continue

                    signal = Signal(
                        category="pattern",
                        text=body[:500],
                        source_type="pr_comment",
                        reviewer=(entry.get("author") or {}).get("login", ""),
                        severity="suggestion",
                        repo=repo,
                        file_path=file_path,
                        code_context=diff_hunk,
                    )
                    db.session.add(signal)
                    created_for_repo += 1

                # Emit live progress every PR so the UI actually moves.
                # total_created + created_for_repo = cumulative across this run.
                update_backfill_progress(
                    prs_done=pr_index,
                    signals_created=total_created + created_for_repo,
                )

            repo_stats["signals_created"] = created_for_repo
            total_created += created_for_repo

        except subprocess.TimeoutExpired:
            repo_stats["error"] = "timeout"
            logger.warning(f"[bootstrap] Timeout scanning {repo}")
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
