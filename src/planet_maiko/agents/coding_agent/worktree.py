"""Git worktree creation, branch management, and cleanup helpers
for coding agents. Each task gets its own worktree on a fresh branch
cut from the latest origin/<default> tip."""

import logging
import os
import subprocess
import uuid

logger = logging.getLogger(__name__)


def _slugify(text, max_len=40):
    slug = text.lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    slug = "-".join(slug.split())
    return slug[:max_len]


def _fetch_latest_base(repo_path):
    """Fetch the latest tip of origin's default branch, and return the
    branch name. Agents should always start from fresh upstream — cutting
    a worktree from a stale local ref means conflict-prone diffs a few
    days later.

    Best-effort: if fetch fails (no network, missing remote, etc.) we
    log and return the best guess of a local branch so _create_worktree
    can still proceed. An offline user gets a local-stale base rather
    than a hard failure.
    """
    default_branch = None

    # Network call; short timeout so an unreachable remote doesn't hang
    # the whole assign flow.
    try:
        fetch_res = subprocess.run(
            ["git", "fetch", "origin", "--prune"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        if fetch_res.returncode != 0:
            logger.warning(
                f"[worktree] git fetch failed for {repo_path}: "
                f"{(fetch_res.stderr or '').strip()[:200]}"
            )
    except Exception as e:
        logger.warning(f"[worktree] git fetch skipped: {e}")

    # Prefer the symbolic ref origin/HEAD points at; fall back to main
    # then master if the local repo never set it. Last resort: whatever
    # the current HEAD is.
    try:
        ref = subprocess.run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=5,
        )
        if ref.returncode == 0:
            # "origin/main" → "main"
            default_branch = ref.stdout.strip().split("/", 1)[-1] or None
    except Exception:
        pass

    if not default_branch:
        for candidate in ("main", "master"):
            check = subprocess.run(
                ["git", "rev-parse", "--verify", f"origin/{candidate}"],
                cwd=repo_path, capture_output=True, text=True,
            )
            if check.returncode == 0:
                default_branch = candidate
                break

    return default_branch


def _fetch_pr_head(repo_path, pr_number):
    """Fetch a PR's head ref into a local branch and return the ref name.

    Uses GitHub's ``pull/<n>/head`` virtual ref so this works for
    forks and same-repo PRs alike — `gh pr checkout` does the same
    under the hood but pulls in the gh CLI which we don't strictly
    need here. Returns the local branch name on success, None on
    failure (network blip, PR not found, repo not on GitHub).
    """
    local_ref = f"maiko-pr-{pr_number}"
    try:
        # Force-update so a re-review against new commits picks up the
        # latest head. The :+ syntax is git's "fast-forward or replace".
        result = subprocess.run(
            ["git", "fetch", "origin",
             f"+pull/{pr_number}/head:{local_ref}"],
            cwd=repo_path, capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return local_ref
        logger.warning(
            f"[worktree] git fetch pull/{pr_number}/head failed: "
            f"{(result.stderr or '').strip()[:200]}"
        )
    except Exception as e:
        logger.warning(f"[worktree] PR head fetch threw: {e}")
    return None


def _create_worktree(repo_path, branch_name, pr_number=None):
    """Create a git worktree on a *new* branch for an agent to work in.

    Coding agents start from the latest origin/<default_branch> tip so
    their work is layered on fresh upstream. Review agents need the
    PR's actual code instead — pass ``pr_number`` and the worktree's
    base ref becomes the PR's head ref (fetched into FETCH_HEAD), so
    `git diff origin/<default>...HEAD` inside the worktree shows the
    PR's full diff and `leave_comment` calls can pin to real lines.

    Uses ``git worktree add -b <branch> <path> <base>`` so the branch
    is always fresh. Without ``-b``, ``git worktree add <path> <branch>``
    will silently reuse an existing branch — and any TASK.md / PLAN.md
    / NOTES.md that a previous agent left behind on that branch leaks
    straight into the next task. If the branch name happens to collide,
    retry once with a uuid suffix.

    Returns the absolute worktree path on success, or None on failure.
    """
    worktree_base = os.path.join(repo_path, ".maiko-worktrees")
    os.makedirs(worktree_base, exist_ok=True)

    default_branch = _fetch_latest_base(repo_path)
    base_ref = f"origin/{default_branch}" if default_branch else None

    # Review path: fetch the PR's head ref into FETCH_HEAD and use it
    # as the worktree base. The new branch points at the PR's head SHA
    # so the agent reviews the actual code under review, not main.
    if pr_number:
        pr_ref = _fetch_pr_head(repo_path, pr_number)
        if pr_ref:
            base_ref = pr_ref
        else:
            logger.warning(
                f"[worktree] Couldn't fetch PR #{pr_number} head — "
                f"falling back to {base_ref}; the agent will see main, "
                f"not the PR's diff"
            )

    candidates = [branch_name, f"{branch_name}-{uuid.uuid4().hex[:6]}"]
    for candidate in candidates:
        worktree_path = os.path.join(worktree_base, candidate)
        if os.path.exists(worktree_path):
            logger.warning(
                f"[worktree] Path {worktree_path} already exists, trying next candidate"
            )
            continue
        try:
            cmd = ["git", "worktree", "add", "-b", candidate, worktree_path]
            if base_ref:
                cmd.append(base_ref)
            result = subprocess.run(
                cmd, cwd=repo_path, capture_output=True, text=True,
            )
        except Exception as e:
            logger.error(f"[worktree] git invocation failed: {e}")
            return None
        if result.returncode == 0:
            if base_ref:
                logger.info(f"[worktree] Created {candidate} from {base_ref}")
            return worktree_path
        # -b fails when the branch already exists — that's the leak we
        # want to avoid. Try the next candidate (uuid-suffixed) before
        # giving up.
        logger.warning(
            f"[worktree] Create failed for {candidate}: "
            f"{(result.stderr or '').strip()[:200]}"
        )

    logger.error(
        f"[worktree] Failed to create worktree after {len(candidates)} attempts"
    )
    return None


def cleanup(repo_path, branch_name):
    """Remove a worktree and its branch after agent is done."""
    worktree_path = os.path.join(repo_path, ".maiko-worktrees", branch_name)
    try:
        subprocess.run(
            ["git", "worktree", "remove", worktree_path, "--force"],
            cwd=repo_path, capture_output=True, text=True,
        )
    except Exception as e:
        logger.warning(f"Worktree cleanup failed: {e}")


def cleanup_task_worktree(task):
    """Best-effort: remove the agent worktree backing this task.

    Called when a task is closed (done / cancelled / deleted) so
    .maiko-worktrees doesn't accumulate stale dirs and we stop
    burning disk on workstreams the user is no longer interested in.

    Idempotent — silently no-ops on tasks without a worktree, paths
    that aren't under .maiko-worktrees (paranoia: never run on a
    user's main checkout), or repos we can't locate.
    """
    extra = task.extra or {}
    wp = extra.get("working_path")
    branch = extra.get("branch")
    if not wp or not branch:
        return
    # Normalize separators so the marker check works on Windows too.
    norm = wp.replace("\\", "/")
    if "/.maiko-worktrees/" not in norm:
        return  # never touch a user-owned path
    repo_path = norm.split("/.maiko-worktrees/", 1)[0]
    if not repo_path or not os.path.isdir(repo_path):
        return
    try:
        cleanup(repo_path, branch)
        logger.info(f"[task] Cleaned up worktree for {task.id}: {wp}")
    except Exception as e:
        logger.warning(f"[task] Worktree cleanup failed for {task.id}: {e}")

