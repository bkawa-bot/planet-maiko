"""Git worktree creation, branch management, and cleanup helpers
for coding agents. Each task gets its own worktree on a fresh branch
cut from the latest origin/<default> tip.

Not every agent run touches code. Planning skills, investigation
agents, and one-off question answerers don't need a git checkout —
forcing one means the user has to pick a repo for jobs that have
nothing to do with one. For those, `_create_scratch_dir` mints a
plain working directory under the maiko data dir; `prepare()` routes
to it when no repo_path is supplied, and cleanup() rmtrees it
instead of calling `git worktree remove`.
"""

import logging
import os
import shutil
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


_SCRATCH_DIRNAME = "scratch-worktrees"


def _scratch_root():
    """Where repo-less agent working dirs live: <data_dir>/scratch-worktrees/.

    Kept under the maiko data dir (not the user's repos) so the marker
    check in cleanup_task_worktree never confuses a scratch dir for a
    git worktree, and so users grepping their repos for `.maiko-worktrees`
    don't see scratch debris.
    """
    from planet_maiko.paths import data_dir
    return os.path.join(data_dir(), _SCRATCH_DIRNAME)


def _create_scratch_dir(job_id):
    """Create a plain working directory for a repo-less agent run.

    For jobs without a scope_repo (planning skills, investigation,
    one-off question answers): we still want an isolated workspace
    where TASK.md / CLAUDE.md / .mcp.json land, but not a git
    worktree. Returns the absolute path on success, None on failure.

    The directory is keyed by AgentJob.id, so a job's scratch dir is
    stable across kickoff retries within the same job — no UUID
    suffix dance needed because AgentJob ids are already unique.
    """
    try:
        root = _scratch_root()
        os.makedirs(root, exist_ok=True)
        path = os.path.join(root, str(job_id))
        os.makedirs(path, exist_ok=True)
        logger.info(f"[worktree] Created scratch dir for {job_id} at {path}")
        return path
    except Exception as e:
        logger.error(f"[worktree] scratch dir creation failed: {e}")
        return None


def _is_scratch_path(path):
    """True if `path` lives under the maiko scratch root.

    Used by cleanup to decide between rmtree and `git worktree remove`.
    Normalizes separators so it works on Windows.
    """
    if not path:
        return False
    norm = os.path.abspath(path).replace("\\", "/")
    root = os.path.abspath(_scratch_root()).replace("\\", "/")
    return norm == root or norm.startswith(root + "/")


def cleanup(repo_path, branch_name):
    """Tear down an agent's working directory.

    Two flavors:

    - Git worktree (the default): repo_path is the parent repo, branch_name
      is the worktree's branch. We compute <repo>/.maiko-worktrees/<branch>
      and run `git worktree remove --force` so the linked checkout +
      branch metadata go away cleanly.

    - Scratch dir (repo-less agents): branch_name is None *or* repo_path
      already points inside the scratch root. We rmtree it directly —
      there's no git metadata to unwind.

    Idempotent: silently no-ops on missing paths so callers can fire
    this in cancel / cleanup paths without checking first.
    """
    # Scratch path: repo_path is itself the working dir (no branch).
    if not branch_name or _is_scratch_path(repo_path):
        if not repo_path or not os.path.isdir(repo_path):
            return
        # Paranoia: only rmtree under the maiko-managed scratch root.
        # Refuse to delete an arbitrary path even if the caller asked.
        if not _is_scratch_path(repo_path):
            logger.warning(
                f"[worktree] refusing scratch cleanup outside scratch root: {repo_path}"
            )
            return
        try:
            shutil.rmtree(repo_path, ignore_errors=True)
            logger.info(f"[worktree] rmtree scratch dir {repo_path}")
        except Exception as e:
            logger.warning(f"[worktree] scratch cleanup failed: {e}")
        return

    worktree_path = os.path.join(repo_path, ".maiko-worktrees", branch_name)
    try:
        subprocess.run(
            ["git", "worktree", "remove", worktree_path, "--force"],
            cwd=repo_path, capture_output=True, text=True,
        )
    except Exception as e:
        logger.warning(f"Worktree cleanup failed: {e}")


def cleanup_task_worktree(task):
    """Best-effort: remove the agent working dir backing this task.

    Called when a task is closed (done / cancelled / deleted) so
    .maiko-worktrees + scratch-worktrees don't accumulate stale dirs
    and we stop burning disk on workstreams the user is no longer
    interested in.

    Handles both flavors:
      - Git worktree: working_path is <repo>/.maiko-worktrees/<branch>;
        derive repo_path and run `git worktree remove`.
      - Scratch dir: working_path lives under <data_dir>/scratch-worktrees;
        rmtree directly (no branch on task.extra in this case).

    Idempotent — silently no-ops on tasks without a working_path or
    paths that aren't under either managed marker (paranoia: never
    run on a user's main checkout).
    """
    extra = task.extra or {}
    wp = extra.get("working_path")
    branch = extra.get("branch")
    if not wp:
        return
    # Scratch dir — no branch, lives under data_dir's scratch root.
    if _is_scratch_path(wp):
        try:
            cleanup(wp, None)
            logger.info(f"[task] Cleaned up scratch dir for {task.id}: {wp}")
        except Exception as e:
            logger.warning(f"[task] Scratch cleanup failed for {task.id}: {e}")
        return
    # Git worktree — only proceed if we can pinpoint the parent repo
    # via the .maiko-worktrees marker, never touch a user-owned path.
    if not branch:
        return
    norm = wp.replace("\\", "/")
    if "/.maiko-worktrees/" not in norm:
        return
    repo_path = norm.split("/.maiko-worktrees/", 1)[0]
    if not repo_path or not os.path.isdir(repo_path):
        return
    try:
        cleanup(repo_path, branch)
        logger.info(f"[task] Cleaned up worktree for {task.id}: {wp}")
    except Exception as e:
        logger.warning(f"[task] Worktree cleanup failed for {task.id}: {e}")

