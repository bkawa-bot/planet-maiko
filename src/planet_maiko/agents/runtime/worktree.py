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


# ---------------------------------------------------------------------------
# Worktree maintenance — periodic sweep for stale dirs
# ---------------------------------------------------------------------------

_TERMINAL_JOB_STATUSES = ("done", "cancelled", "failed")


def _dir_size_bytes(path):
    """Best-effort recursive size in bytes. Returns 0 on any error so a
    permissions failure on one subtree doesn't break the whole stats
    walk."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _enumerate_managed_worktrees():
    """Walk every worktree currently on disk that Planet Maiko owns.

    Returns a list of dicts: {path, kind, job_id, size_bytes, mtime}.
      kind   — "scratch" | "git"
      job_id — best-guess id (scratch dirs are keyed by job_id directly;
               git worktrees encode the branch in the path, which doesn't
               trivially reverse to a job — left as None and matched up
               via AgentJob.worktree_path at the call site)
      mtime  — directory mtime as a unix timestamp

    Doesn't touch the DB — just a filesystem scan. The caller is
    responsible for cross-referencing with AgentJob.
    """
    out = []

    # Scratch dirs live under <data_dir>/scratch-worktrees/<job_id>/
    root = _scratch_root()
    if os.path.isdir(root):
        for name in os.listdir(root):
            full = os.path.join(root, name)
            if not os.path.isdir(full):
                continue
            try:
                stat = os.stat(full)
            except OSError:
                continue
            out.append({
                "path": full,
                "kind": "scratch",
                "job_id": name,
                "size_bytes": _dir_size_bytes(full),
                "mtime": stat.st_mtime,
            })

    # Git worktrees live under each configured repo's .maiko-worktrees/.
    # Walk the configured repos rather than the whole filesystem.
    try:
        from planet_maiko.config import load_config
        from planet_maiko.orchestration import resolve_repo_path
        cfg_github = (load_config().get("github") or {})
        for repo in (cfg_github.get("repos") or []):
            local = resolve_repo_path(repo)
            if not local:
                continue
            wt_root = os.path.join(local, ".maiko-worktrees")
            if not os.path.isdir(wt_root):
                continue
            for name in os.listdir(wt_root):
                full = os.path.join(wt_root, name)
                if not os.path.isdir(full):
                    continue
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                out.append({
                    "path": full,
                    "kind": "git",
                    "job_id": None,  # resolved by worktree_path lookup
                    "branch": name,
                    "repo_path": local,
                    "size_bytes": _dir_size_bytes(full),
                    "mtime": stat.st_mtime,
                })
    except Exception as e:
        logger.debug(f"[worktree] git-worktree enumeration skipped: {e}")

    return out


def worktree_stats():
    """Return a snapshot of every Planet-Maiko-managed worktree on disk.

    Used by the Settings page to show what's accumulating. Returns:
        {
          "total_count": int,
          "total_bytes": int,
          "oldest_mtime": float | None,
          "scratch_count": int,
          "git_count": int,
        }
    """
    entries = _enumerate_managed_worktrees()
    total_count = len(entries)
    total_bytes = sum(e["size_bytes"] for e in entries)
    oldest = min((e["mtime"] for e in entries), default=None)
    scratch_count = sum(1 for e in entries if e["kind"] == "scratch")
    git_count = sum(1 for e in entries if e["kind"] == "git")
    return {
        "total_count": total_count,
        "total_bytes": total_bytes,
        "oldest_mtime": oldest,
        "scratch_count": scratch_count,
        "git_count": git_count,
    }


def sweep_old_worktrees(max_age_days):
    """Remove worktrees older than `max_age_days` whose AgentJob is in a
    terminal state (done / cancelled / failed) — or whose AgentJob no
    longer exists at all (orphan dirs).

    Active worktrees (queued / running jobs) are never touched
    regardless of age. The intent is recovering disk space from agent
    runs the user has clearly moved on from, not yanking the rug from
    under in-flight work.

    Returns a dict with what happened:
        {
          "scanned": int,
          "removed": int,
          "skipped_active": int,
          "skipped_recent": int,
          "freed_bytes": int,
        }
    """
    if not max_age_days or max_age_days <= 0:
        return {"scanned": 0, "removed": 0, "skipped_active": 0,
                "skipped_recent": 0, "freed_bytes": 0, "disabled": True}

    import time
    from planet_maiko.models.agent_job import AgentJob
    from planet_maiko.database import db

    cutoff = time.time() - (max_age_days * 86400)
    entries = _enumerate_managed_worktrees()

    # Build a quick lookup of every AgentJob.worktree_path that's still
    # owned by an active job. We do this rather than a one-by-one DB
    # query per entry so the sweep is bounded at one query for the
    # whole pass.
    active_paths = set()
    job_by_path = {}
    try:
        jobs = AgentJob.query.filter(
            AgentJob.worktree_path.isnot(None)
        ).all()
        for j in jobs:
            wp = (j.worktree_path or "").replace("\\", "/")
            if not wp:
                continue
            job_by_path[wp] = j
            if j.status not in _TERMINAL_JOB_STATUSES:
                active_paths.add(wp)
    except Exception as e:
        logger.warning(f"[worktree-sweep] DB lookup failed, aborting: {e}")
        return {"scanned": 0, "removed": 0, "skipped_active": 0,
                "skipped_recent": 0, "freed_bytes": 0, "error": str(e)}

    removed = 0
    freed_bytes = 0
    skipped_active = 0
    skipped_recent = 0

    for entry in entries:
        norm = entry["path"].replace("\\", "/")
        if norm in active_paths:
            skipped_active += 1
            continue
        if entry["mtime"] > cutoff:
            skipped_recent += 1
            continue
        # OK to remove. Route through the existing cleanup() so git
        # worktrees go through `git worktree remove` and scratch dirs
        # rmtree through the scratch-root safety check.
        try:
            if entry["kind"] == "scratch":
                cleanup(entry["path"], None)
            else:
                cleanup(entry.get("repo_path"), entry.get("branch"))
            removed += 1
            freed_bytes += entry["size_bytes"]
            logger.info(
                f"[worktree-sweep] removed {entry['kind']} worktree "
                f"{entry['path']} (age {int((time.time() - entry['mtime'])/86400)}d)"
            )
        except Exception as e:
            logger.warning(
                f"[worktree-sweep] failed to remove {entry['path']}: {e}"
            )

    return {
        "scanned": len(entries),
        "removed": removed,
        "skipped_active": skipped_active,
        "skipped_recent": skipped_recent,
        "freed_bytes": freed_bytes,
    }

