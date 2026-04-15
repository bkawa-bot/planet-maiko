import json
import logging
import subprocess

from planet_maiko.pollers.base import BasePoller

logger = logging.getLogger(__name__)


def _detect_language(file_path):
    """Detect language from file extension."""
    if not file_path:
        return None
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    return {
        "py": "python", "java": "java", "js": "javascript", "ts": "typescript",
        "jsx": "javascript", "tsx": "typescript", "rb": "ruby", "go": "go",
        "rs": "rust", "kt": "kotlin", "swift": "swift",
    }.get(ext)


class GitHubPoller(BasePoller):
    """Polls GitHub for PR activity using the gh CLI.

    Generates pupdates for:
        - PRs where your review is requested
        - Your PRs that received approvals
        - Your PRs that received change requests
        - Your PRs with failing CI checks
    """

    @property
    def name(self):
        return "github"

    def _gh(self, args):
        """Run a gh CLI command and return parsed JSON."""
        cmd = ["gh"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"gh command failed: {result.stderr.strip()}")
        return json.loads(result.stdout) if result.stdout.strip() else []

    def _get_review_requests(self, username):
        """Get PRs where the user's review is requested."""
        return self._gh([
            "search", "prs",
            "--review-requested", username,
            "--state", "open",
            "--json", "number,title,url,repository,author,createdAt,labels",
        ])

    def _get_my_prs(self, username):
        """Get the user's open PRs with review status."""
        return self._gh([
            "search", "prs",
            "--author", username,
            "--state", "open",
            "--json", "number,title,url,repository,author,createdAt,labels",
        ])

    def _get_merged_prs_for_repos(self, repos):
        """Get recently merged PRs across all configured repos (for training)."""
        merged = []
        for repo in repos:
            try:
                result = subprocess.run(
                    ["gh", "pr", "list", "--repo", repo, "--state", "merged",
                     "--limit", "5", "--json", "number,title,url,author,mergedAt,labels"],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0:
                    prs = json.loads(result.stdout)
                    for pr in prs:
                        pr["_repo"] = repo
                    merged.extend(prs)
            except Exception:
                pass
        return merged

    def _get_pr_reviews(self, repo, pr_number):
        """Get reviews for a specific PR."""
        try:
            return self._gh([
                "api", f"repos/{repo}/pulls/{pr_number}/reviews",
                "--jq", ".",
            ])
        except Exception:
            # Fallback: use pr view
            try:
                result = subprocess.run(
                    ["gh", "pr", "view", str(pr_number),
                     "--repo", repo,
                     "--json", "reviews"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    return data.get("reviews", [])
            except Exception:
                pass
            return []

    def _get_pr_checks(self, repo, pr_number):
        """Get CI check status for a PR."""
        try:
            result = subprocess.run(
                ["gh", "pr", "view", str(pr_number),
                 "--repo", repo,
                 "--json", "statusCheckRollup"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return data.get("statusCheckRollup", [])
        except Exception:
            pass
        return []

    def _get_pr_comments(self, repo, pr_number):
        """Get review comments (inline code feedback) for a PR.

        Fetches both issue-level comments and inline review comments
        (which include file path information for language detection).
        """
        comments = []

        # Issue-level comments (no file path)
        try:
            result = subprocess.run(
                ["gh", "pr", "view", str(pr_number),
                 "--repo", repo,
                 "--json", "reviewRequests,comments"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                for c in data.get("comments", []):
                    c["_type"] = "issue_comment"
                    comments.append(c)
        except Exception:
            pass

        # Inline review comments (have file path via the API)
        try:
            inline = self._gh([
                "api", f"repos/{repo}/pulls/{pr_number}/comments",
                "--jq", ".",
            ])
            for c in inline:
                comments.append({
                    "author": {"login": c.get("user", {}).get("login", "unknown")},
                    "body": c.get("body", ""),
                    "path": c.get("path", ""),
                    "_type": "review_comment",
                })
        except Exception:
            pass

        return comments

    def poll(self, config):
        """Fetch all relevant GitHub data."""
        username = config.get("username", "")
        if not username:
            logger.warning("[github] No username configured, skipping poll")
            return {"review_requests": [], "my_prs": [], "review_comments": []}

        review_requests = self._get_review_requests(username)
        my_prs = self._get_my_prs(username)
        repos = config.get("repos", [])
        merged_prs = self._get_merged_prs_for_repos(repos)

        # For each of the user's PRs, check review and CI status + comments
        review_comments = []
        for pr in my_prs:
            repo = pr.get("repository", {}).get("nameWithOwner", "")
            number = pr.get("number")
            if repo and number:
                pr["_reviews"] = self._get_pr_reviews(repo, number)
                pr["_checks"] = self._get_pr_checks(repo, number)
                comments = self._get_pr_comments(repo, number)
                pr["_comments"] = comments
                # Stash the latest comment timestamp on the pr so
                # to_pupdates can use it as the source_id seed —
                # changes when a genuinely new comment arrives, stays
                # stable otherwise (so dedup works).
                latest = ""
                for c in comments:
                    ts = c.get("created_at") or c.get("updated_at") or ""
                    if ts > latest:
                        latest = ts
                pr["_latest_comment_at"] = latest
                pr["_comment_count"] = len(comments)
                for c in comments:
                    review_comments.append({
                        "type": c.get("_type", "issue_comment"),
                        "repo": repo,
                        "pr_number": number,
                        "author": c.get("author", {}).get("login", "unknown"),
                        "body": c.get("body", ""),
                        "file_path": c.get("path", ""),
                    })

        return {
            "review_requests": review_requests,
            "my_prs": my_prs,
            "merged_prs": merged_prs,
            "review_comments": review_comments,
        }

    def to_pupdates(self, raw_data):
        """Transform GitHub data into pupdates."""
        pupdates = []

        # Build a lookup of open Maiko-coding-task PR URLs so we can
        # emit pr_review_commented events targeting our own agent
        # rather than treating those PRs like generic external feedback.
        # We match either task.url or task.extra.pr_url since both are
        # set by the approve flow.
        from planet_maiko.models.task import Task
        try:
            open_coding_tasks = Task.query.filter(
                Task.status.in_(["new", "in_progress", "in_review"]),
            ).all()
        except Exception:
            open_coding_tasks = []
        task_by_pr_url = {}
        for t in open_coding_tasks:
            if t.url:
                task_by_pr_url[t.url.rstrip("/")] = t
            extra_url = (t.extra or {}).get("pr_url")
            if extra_url:
                task_by_pr_url[extra_url.rstrip("/")] = t

        # Review requests -> high priority pupdates
        for pr in raw_data.get("review_requests", []):
            repo = pr.get("repository", {}).get("nameWithOwner", "")
            number = pr.get("number")
            author = pr.get("author", {}).get("login", "unknown")
            labels = [l.get("name", "") for l in pr.get("labels", [])]

            pupdates.append({
                "source_id": f"review/{repo}#{number}",
                "type": "pr_review_requested",
                "priority": "high",
                "title": f"Review requested: {pr.get('title', '')}",
                "body": f"{author} requested your review on {repo}#{number}",
                "url": pr.get("url", ""),
                "actionable": True,
                "action_hint": "Review PR",
                "tags": [repo.split("/")[-1]] + labels,
                "metadata": {
                    "repo": repo,
                    "number": number,
                    "author": author,
                },
            })

        # My PRs - check for approvals, changes requested, CI failures
        for pr in raw_data.get("my_prs", []):
            repo = pr.get("repository", {}).get("nameWithOwner", "")
            number = pr.get("number")
            reviews = pr.get("_reviews", [])
            checks = pr.get("_checks", [])
            labels = [l.get("name", "") for l in pr.get("labels", [])]

            # Always create a pupdate for open PRs (so the user sees them)
            author = pr.get("author", {}).get("login", "unknown")
            pupdates.append({
                "source_id": f"open/{repo}#{number}",
                "type": "my_pr_open",
                "priority": "low",
                "title": f"Open PR: {pr.get('title', '')}",
                "body": f"Your PR {repo}#{number} is open",
                "url": pr.get("url", ""),
                "actionable": False,
                "tags": [repo.split("/")[-1]] + labels,
                "metadata": {
                    "repo": repo,
                    "number": number,
                    "author": author,
                },
            })

            # Count review states
            approved = sum(1 for r in reviews if r.get("state") == "APPROVED")
            changes_requested = sum(
                1 for r in reviews if r.get("state") == "CHANGES_REQUESTED"
            )

            if changes_requested > 0:
                pupdates.append({
                    "source_id": f"changes/{repo}#{number}",
                    "type": "pr_changes_requested",
                    "priority": "high",
                    "title": f"Changes requested: {pr.get('title', '')}",
                    "body": f"{changes_requested} reviewer(s) requested changes on {repo}#{number}",
                    "url": pr.get("url", ""),
                    "actionable": True,
                    "action_hint": "Address feedback",
                    "tags": [repo.split("/")[-1]] + labels,
                    "metadata": {
                        "repo": repo,
                        "number": number,
                        "approved": approved,
                        "changes_requested": changes_requested,
                    },
                })
            elif approved > 0:
                pupdates.append({
                    "source_id": f"approved/{repo}#{number}",
                    "type": "pr_approved",
                    "priority": "normal",
                    "title": f"PR approved: {pr.get('title', '')}",
                    "body": f"{approved} approval(s) on {repo}#{number}",
                    "url": pr.get("url", ""),
                    "actionable": True,
                    "action_hint": "Merge PR",
                    "tags": [repo.split("/")[-1]] + labels,
                    "metadata": {
                        "repo": repo,
                        "number": number,
                        "approved": approved,
                    },
                })

            # CI failures
            failed_checks = [
                c for c in checks
                if c.get("conclusion") == "FAILURE"
                or c.get("status") == "FAILURE"
            ]
            if failed_checks:
                check_names = [c.get("name", "unknown") for c in failed_checks[:3]]
                pupdates.append({
                    "source_id": f"ci-fail/{repo}#{number}",
                    "type": "pr_ci_failed",
                    "priority": "high",
                    "title": f"CI failing: {pr.get('title', '')}",
                    "body": f"Failed checks on {repo}#{number}: {', '.join(check_names)}",
                    "url": pr.get("url", ""),
                    "actionable": True,
                    "action_hint": "Fix CI",
                    "tags": [repo.split("/")[-1], "ci"] + labels,
                    "metadata": {
                        "repo": repo,
                        "number": number,
                        "failed_checks": check_names,
                    },
                })

            # PR comments on a Maiko-owned coding task → wake the agent
            # to fetch + address them. Source_id includes the latest
            # comment timestamp so each genuinely new batch fires once;
            # the agent uses `gh pr view N --comments` to read the
            # actual content rather than us shipping it.
            pr_url = (pr.get("url") or "").rstrip("/")
            owning_task = task_by_pr_url.get(pr_url)
            comment_count = pr.get("_comment_count", 0)
            latest_at = pr.get("_latest_comment_at", "")
            if owning_task and comment_count and latest_at:
                pupdates.append({
                    "source_id": f"pr-comment/{repo}#{number}/{latest_at}",
                    "type": "pr_review_commented",
                    "priority": "normal",
                    "title": f"New PR feedback: {pr.get('title', '')}",
                    "body": f"{comment_count} comment(s) on {pr.get('url', '')}",
                    "url": pr.get("url", ""),
                    "actionable": True,
                    "action_hint": "Address feedback",
                    "tags": [repo.split("/")[-1], "pr-feedback", owning_task.id],
                    "metadata": {
                        "repo": repo,
                        "number": number,
                        "task_id": owning_task.id,
                        "pr_url": pr.get("url", ""),
                        "comment_count": comment_count,
                        "latest_comment_at": latest_at,
                    },
                })

        # Merged PRs (from all configured repos — for training + task completion)
        for pr in raw_data.get("merged_prs", []):
            repo = pr.get("_repo", "")
            number = pr.get("number")
            author = pr.get("author", {}).get("login", "unknown") if isinstance(pr.get("author"), dict) else "unknown"
            labels = [l.get("name", "") for l in pr.get("labels", [])] if pr.get("labels") else []
            pupdates.append({
                "source_id": f"merged/{repo}#{number}",
                "type": "pr_merged",
                "priority": "low",
                "title": f"PR merged: {pr.get('title', '')}",
                "body": f"{author} merged {repo}#{number}",
                "url": pr.get("url", ""),
                "actionable": False,
                "tags": [repo.split("/")[-1]] + labels,
                "metadata": {
                    "repo": repo,
                    "number": number,
                    "author": author,
                    "merged_at": pr.get("mergedAt"),
                },
            })

        return pupdates

    def to_signals(self, raw_data):
        """Extract review comments as learning signals."""
        signals = []
        for item in raw_data.get("review_comments", []):
            comment = item.get("body", "").strip()
            if not comment or len(comment) < 20:
                continue
            signals.append({
                "category": "pattern",  # Placeholder — real category set by synthesizer
                "text": comment[:500],
                "source_type": "pr_comment",
                "reviewer": item.get("author", ""),
                "severity": "suggestion",
                "repo": item.get("repo", ""),
                "file_path": item.get("file_path", ""),
                "language": _detect_language(item.get("file_path", "")),
            })
        return signals

    def _after_sync(self, raw_data, db_session):
        """For each merged PR in this poll, fetch inline review comments
        and create unsynthesized Signal rows with code_context. Dedupes
        per-repo against existing signals by (text, file_path, diff_hunk)
        so overlapping polls don't duplicate.

        Signals land as synthesized=False — they flow through the normal
        synthesis → clustering pipeline on the next brain cycle tick.
        """
        merged = raw_data.get("merged_prs") or []
        if not merged:
            return

        from planet_maiko.models.signal import Signal
        from planet_maiko.brain.learning.bootstrap import fetch_comments_for_pr

        # Group merged PRs by repo so we can preload once per repo.
        by_repo = {}
        for pr in merged:
            repo = pr.get("_repo", "")
            if not repo:
                continue
            by_repo.setdefault(repo, []).append(pr)

        created = 0
        for repo, prs in by_repo.items():
            existing_keys = {
                (s.text, s.file_path or "", s.code_context or "")
                for s in Signal.query.filter_by(
                    repo=repo, source_type="pr_comment"
                ).all()
            }

            for pr in prs:
                number = pr.get("number")
                if not number:
                    continue
                comments = fetch_comments_for_pr(repo, number)
                for entry in comments:
                    body = entry["body"][:500]
                    file_path = entry.get("path") or None
                    diff_hunk = entry.get("diff_hunk") or None
                    key = (body, file_path or "", diff_hunk or "")
                    if key in existing_keys:
                        continue
                    sig = Signal(
                        category="pattern",  # placeholder — synthesis sets the real one
                        text=body,
                        source_type="pr_comment",
                        reviewer=entry.get("author", "") or "",
                        severity="suggestion",
                        repo=repo,
                        file_path=file_path,
                        code_context=diff_hunk,
                        examples=[{
                            "path": file_path,
                            "diff_hunk": diff_hunk,
                            "author": entry.get("author", "") or "",
                            "line": entry.get("line"),
                        }] if diff_hunk else [],
                        synthesized=False,  # will be synthesized next cycle
                    )
                    db_session.add(sig)
                    existing_keys.add(key)
                    created += 1

        if created:
            logger.info(f"[{self.name}] Scraped {created} inline comment signal(s) from merged PRs")
