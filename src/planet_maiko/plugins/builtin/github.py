"""GitHub plugin. Polls for PR activity via the gh CLI.

Generates pupdates for:
    - PRs where your review is requested
    - Your PRs that received approvals
    - Your PRs that received change requests
    - Your PRs with failing CI checks
    - New comments on PRs from Maiko-owned coding tasks
"""

import json
import logging
import subprocess

from planet_maiko.plugins.poller import PollerPlugin

logger = logging.getLogger(__name__)


class GitHubPlugin(PollerPlugin):
    name = "github"

    def get_config_defaults(self):
        return {"github": {"enabled": False, "poll_interval_minutes": 5,
                           "username": "", "repos": [], "repo_roots": []}}

    def get_config_schema(self):
        return {
            "enabled": {"type": "bool", "label": "Enabled"},
            "username": {
                "type": "string", "label": "GitHub username",
                "placeholder": "your-github-username",
                "help": "Requires the gh CLI installed and authenticated (gh auth login).",
            },
            "repos": {
                "type": "list", "label": "Repos",
                "placeholder": "org/repo, org/other-repo",
                "help": "Repos watched for merged-PR signals. Use Discover to fill from recent activity.",
            },
            "repo_roots": {
                "type": "list", "label": "Repository roots",
                "placeholder": "~/src, ~/projects",
                "help": "Local paths where your repos live on disk. Used for agent worktrees.",
            },
            "poll_interval_minutes": {
                "type": "number", "label": "Poll interval (minutes)",
            },
        }

    def get_setup_actions(self):
        return [
            {"key": "test_connection", "label": "Test connection", "sync": True,
             "description": "Check the gh CLI is installed and authenticated."},
            {"key": "discover_repos", "label": "Discover repos", "sync": True,
             "description": "Find repos you've pushed to recently and add them above."},
        ]

    def run_setup_action(self, key):
        if key == "test_connection":
            return {"ok": True, "message": f"Connected as {self._gh_user()}"}
        if key == "discover_repos":
            return self._discover_repos()
        return super().run_setup_action(key)

    @staticmethod
    def _gh_user():
        """Authenticated gh login, or raise with a reason the form shows."""
        try:
            result = subprocess.run(
                ["gh", "api", "user", "--jq", ".login"],
                capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "gh CLI not installed. Install it from https://cli.github.com"
            )
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip()[:200]
                or "gh auth check failed. Run: gh auth login"
            )
        login = result.stdout.strip()
        if not login:
            raise RuntimeError("gh returned no user. Run: gh auth login")
        return login

    def _discover_repos(self):
        """Find repos the user pushed to recently; merge into config.repos."""
        import shutil as _shutil
        from datetime import datetime, timezone, timedelta

        cfg = self._get_config()
        username = cfg.get("username", "")
        if not username:
            raise RuntimeError("Set your GitHub username first, then discover.")
        if not _shutil.which("gh"):
            raise RuntimeError(
                "gh CLI not found. Install it from https://cli.github.com"
            )
        auth = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=5,
        )
        if auth.returncode != 0:
            raise RuntimeError("gh CLI isn't authenticated. Run: gh auth login")

        cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
        found = []
        result = subprocess.run(
            ["gh", "api",
             f"/search/commits?q=author:{username}+committer-date:>{cutoff}"
             "&sort=committer-date&per_page=30"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout or "{}")
            seen = set()
            for item in data.get("items", []):
                repo_name = (item.get("repository") or {}).get("full_name", "")
                if repo_name and repo_name not in seen:
                    seen.add(repo_name)
                    found.append(repo_name)
            found = found[:10]
        else:
            fallback = subprocess.run(
                ["gh", "repo", "list", username, "--limit", "20",
                 "--json", "nameWithOwner", "--jq", ".[].nameWithOwner"],
                capture_output=True, text=True, timeout=15,
            )
            if fallback.returncode != 0:
                raise RuntimeError(
                    f"gh CLI failed: {fallback.stderr.strip()[:200]}"
                )
            found = [r.strip() for r in fallback.stdout.strip().split("\n") if r.strip()][:10]

        if not found:
            return {"ok": False, "message": "No repos found from recent activity."}
        existing = list(cfg.get("repos") or [])
        merged = existing + [r for r in found if r not in existing]
        added = len(merged) - len(existing)
        return {
            "ok": True,
            "message": f"Found {len(found)} repo(s), {added} new.",
            "config_patch": {"repos": merged},
        }

    def _gh(self, args):
        """Run a gh CLI command and return parsed JSON."""
        cmd = ["gh"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"gh command failed: {result.stderr.strip()}")
        return json.loads(result.stdout) if result.stdout.strip() else []

    def _get_review_requests(self, username):
        """PRs where the user is *individually* tagged for review.

        Uses `user-review-requested:` (not `--review-requested`) so team-level
        review requests don't fire pupdates on PRs the user isn't personally
        tagged on. `gh search prs --json` doesn't accept headRefOid; enrich
        each hit via `gh pr view` so source_id can include the SHA and
        re-requests on new commits aren't dedup-swallowed.
        """
        prs = self._gh([
            "search", "prs",
            f"user-review-requested:{username}",
            "--state", "open",
            "--json", "number,title,url,repository,author,createdAt,labels",
        ])
        for pr in prs:
            repo = (pr.get("repository") or {}).get("nameWithOwner")
            number = pr.get("number")
            if not repo or not number:
                continue
            try:
                detail = subprocess.run(
                    ["gh", "pr", "view", str(number),
                     "--repo", repo, "--json", "headRefOid"],
                    capture_output=True, text=True, timeout=15,
                )
                if detail.returncode == 0 and detail.stdout.strip():
                    data = json.loads(detail.stdout)
                    sha = (data or {}).get("headRefOid")
                    if sha:
                        pr["headRefOid"] = sha
            except Exception:
                # Missing SHA falls back to the plain review/repo#N source_id.
                pass
        return prs

    def _get_my_prs(self, username):
        return self._gh([
            "search", "prs",
            "--author", username,
            "--state", "open",
            "--json", "number,title,url,repository,author,createdAt,labels",
        ])

    def _get_merged_prs_for_repos(self, repos):
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
        try:
            return self._gh([
                "api", f"repos/{repo}/pulls/{pr_number}/reviews",
                "--jq", ".",
            ])
        except Exception:
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
        """Issue-level + inline review comments for a PR."""
        comments = []

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
        username = config.get("username", "")
        if not username:
            logger.warning("[github] No username configured, skipping poll")
            return {"review_requests": [], "my_prs": [], "review_comments": []}

        review_requests = self._get_review_requests(username)
        my_prs = self._get_my_prs(username)
        repos = config.get("repos", [])
        merged_prs = self._get_merged_prs_for_repos(repos)

        # For each of the user's PRs, check review + CI + comments status.
        # Open-PR comments drive the pr_review_commented pupdate; training
        # signals come from merged PRs via _after_sync.
        for pr in my_prs:
            repo = pr.get("repository", {}).get("nameWithOwner", "")
            number = pr.get("number")
            if repo and number:
                pr["_reviews"] = self._get_pr_reviews(repo, number)
                pr["_checks"] = self._get_pr_checks(repo, number)
                comments = self._get_pr_comments(repo, number)
                pr["_comments"] = comments
                # Latest comment timestamp seeds source_id so dedup
                # advances when a genuinely new comment arrives.
                latest = ""
                for c in comments:
                    ts = c.get("created_at") or c.get("updated_at") or ""
                    if ts > latest:
                        latest = ts
                pr["_latest_comment_at"] = latest
                pr["_comment_count"] = len(comments)

        return {
            "review_requests": review_requests,
            "my_prs": my_prs,
            "merged_prs": merged_prs,
        }

    def to_pupdates(self, raw_data):
        pupdates = []

        # Look up open Maiko-coding-task PR URLs so we can target our own
        # agent on pr_review_commented events instead of treating those
        # PRs like generic external feedback.
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

        # Review requests -> high priority pupdates. source_id includes the
        # head SHA so a re-request on a new commit isn't dedup-swallowed.
        # Falls back to the plain `review/repo#number` form when gh
        # doesn't return headRefOid.
        for pr in raw_data.get("review_requests", []):
            repo = pr.get("repository", {}).get("nameWithOwner", "")
            number = pr.get("number")
            author = pr.get("author", {}).get("login", "unknown")
            labels = [l.get("name", "") for l in pr.get("labels", [])]
            head_sha = pr.get("headRefOid") or ""

            source_id = f"review/{repo}#{number}"
            if head_sha:
                source_id = f"{source_id}@{head_sha[:10]}"

            pupdates.append({
                "source_id": source_id,
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

        # My PRs: check for approvals, changes requested, CI failures
        for pr in raw_data.get("my_prs", []):
            repo = pr.get("repository", {}).get("nameWithOwner", "")
            number = pr.get("number")
            reviews = pr.get("_reviews", [])
            checks = pr.get("_checks", [])
            labels = [l.get("name", "") for l in pr.get("labels", [])]

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

            # PR comments on a Maiko-owned coding task wake the agent to
            # fetch + address them. source_id includes the latest comment
            # timestamp so each genuinely new batch fires once; the agent
            # uses `gh pr view N --comments` to read the actual content.
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

        # Merged PRs across configured repos (training + task completion)
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
        """No-op. Training signals come from merged PRs via _after_sync.

        Emitting a Signal per review comment on every poll filled the
        Signal table with exact duplicates indefinitely. _after_sync
        scrapes inline comments from merged PRs only and dedupes per repo.
        """
        return []

    def _after_sync(self, raw_data, db_session):
        """Scrape inline review comments from merged PRs and create
        unsynthesized Signal rows for the learning pipeline.

        Three phases: read existing dedup keys, do all network calls with
        no DB activity, then a single fast write batch + commit. Keeps the
        SQLite write lock from being held across slow per-PR API calls.

        Signals land as synthesized=False, then flow through synthesis to
        clustering on the next brain cycle tick.
        """
        merged = raw_data.get("merged_prs") or []
        if not merged:
            return

        from planet_maiko.models.signal import Signal
        from planet_maiko.brain.learning.bootstrap import fetch_comments_for_pr

        by_repo = {}
        for pr in merged:
            repo = pr.get("_repo", "")
            if not repo:
                continue
            by_repo.setdefault(repo, []).append(pr)

        # PHASE 1: read existing dedup keys per repo + check which PRs
        # have already been scraped. The merged-PR list has no time cursor
        # so we use the pr_merged Pupdate row as the cursor: if it carries
        # comments_scraped_at on its extra, we've already pulled comments.
        from planet_maiko.models.pupdate import Pupdate
        from planet_maiko.plugins.base import _pupdate_id

        existing_per_repo = {}
        scraped_pupdate_by_pr = {}
        for repo in by_repo:
            existing_ids = set()
            existing_legacy = set()  # (file_path, diff_hunk)
            existing_text_keys = set()  # (file_path, body[:120]) last-resort
            for s in Signal.query.filter_by(
                repo=repo, source_type="pr_comment"
            ).all():
                if s.external_id:
                    existing_ids.add(s.external_id)
                if s.file_path and s.code_context:
                    existing_legacy.add((s.file_path, s.code_context))
                raw = (s.original_text or s.text or "")[:120]
                if s.file_path and raw:
                    existing_text_keys.add((s.file_path, raw))
            existing_per_repo[repo] = (
                existing_ids, existing_legacy, existing_text_keys,
            )

            for pr in by_repo[repo]:
                number = pr.get("number")
                if not number:
                    continue
                source_id = f"merged/{repo}#{number}"
                pup_id = _pupdate_id(self.name, source_id)
                pup = db_session.get(Pupdate, pup_id)
                if pup:
                    scraped_pupdate_by_pr[(repo, number)] = pup
        db_session.rollback()

        # PHASE 2: network calls. Build a list of Signal kwargs; no DB
        # activity here.
        new_rows = []
        scraped_now = []
        for repo, prs in by_repo.items():
            existing_ids, existing_legacy, existing_text_keys = existing_per_repo[repo]
            for pr in prs:
                number = pr.get("number")
                if not number:
                    continue
                pup = scraped_pupdate_by_pr.get((repo, number))
                if pup and (pup.extra or {}).get("comments_scraped_at"):
                    continue
                comments = fetch_comments_for_pr(repo, number)
                scraped_now.append((repo, number))
                for entry in comments:
                    external_id = entry.get("id") or None
                    body = entry["body"][:500]
                    file_path = entry.get("path") or None
                    diff_hunk = entry.get("diff_hunk") or None

                    if external_id and external_id in existing_ids:
                        continue
                    if (
                        file_path
                        and diff_hunk
                        and (file_path, diff_hunk) in existing_legacy
                    ):
                        continue
                    if (
                        file_path
                        and (file_path, body[:120]) in existing_text_keys
                    ):
                        continue

                    new_rows.append({
                        "category": "pattern",
                        "text": body,
                        "source_type": "pr_comment",
                        "reviewer": entry.get("author", "") or "",
                        "severity": "suggestion",
                        "repo": repo,
                        "file_path": file_path,
                        "code_context": diff_hunk,
                        "external_id": external_id,
                        "examples": [{
                            "path": file_path,
                            "diff_hunk": diff_hunk,
                            "author": entry.get("author", "") or "",
                            "line": entry.get("line"),
                        }] if diff_hunk else [],
                        "synthesized": False,
                    })
                    if external_id:
                        existing_ids.add(external_id)
                    if file_path and diff_hunk:
                        existing_legacy.add((file_path, diff_hunk))
                    if file_path:
                        existing_text_keys.add((file_path, body[:120]))

        # PHASE 3: write batch + cursor flag, single commit.
        if not new_rows and not scraped_now:
            return

        from datetime import datetime as _dt, timezone as _tz
        scraped_at = _dt.now(_tz.utc).isoformat()

        for kwargs in new_rows:
            db_session.add(Signal(**kwargs))

        for (repo, number) in scraped_now:
            pup = scraped_pupdate_by_pr.get((repo, number))
            if pup is None:
                pup_id = _pupdate_id(self.name, f"merged/{repo}#{number}")
                pup = db_session.get(Pupdate, pup_id)
            if pup is not None:
                extra = dict(pup.extra or {})
                extra["comments_scraped_at"] = scraped_at
                pup.extra = extra

        db_session.commit()
        if new_rows:
            logger.info(
                f"[{self.name}] Scraped {len(new_rows)} inline comment "
                f"signal(s) from {len(scraped_now)} PR(s)"
            )
