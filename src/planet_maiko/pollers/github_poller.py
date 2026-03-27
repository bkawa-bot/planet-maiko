import json
import logging
import subprocess

from planet_maiko.pollers.base import BasePoller

logger = logging.getLogger(__name__)


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
        cmd = ["gh"] + args + ["--json"]
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
            "number,title,url,repository,author,createdAt,labels",
        ])

    def _get_my_prs(self, username):
        """Get the user's open PRs with review status."""
        return self._gh([
            "search", "prs",
            "--author", username,
            "--state", "open",
            "number,title,url,repository,createdAt,labels",
        ])

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

    def poll(self, config):
        """Fetch all relevant GitHub data."""
        username = config.get("username", "")
        if not username:
            logger.warning("[github] No username configured, skipping poll")
            return {"review_requests": [], "my_prs": []}

        review_requests = self._get_review_requests(username)
        my_prs = self._get_my_prs(username)

        # For each of the user's PRs, check review and CI status
        for pr in my_prs:
            repo = pr.get("repository", {}).get("nameWithOwner", "")
            number = pr.get("number")
            if repo and number:
                pr["_reviews"] = self._get_pr_reviews(repo, number)
                pr["_checks"] = self._get_pr_checks(repo, number)

        return {
            "review_requests": review_requests,
            "my_prs": my_prs,
        }

    def to_pupdates(self, raw_data):
        """Transform GitHub data into pupdates."""
        pupdates = []

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

        return pupdates
