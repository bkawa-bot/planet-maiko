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
        cmd = ["gh"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"gh command failed: {result.stderr.strip()}")
        return json.loads(result.stdout) if result.stdout.strip() else []

    def _get_review_requests(self, username):
        """Get PRs where the user is *individually* tagged for review.

        The `--review-requested` flag (and the `review-requested:` query
        qualifier it maps to) also matches team-level review requests
        when the user is a member of that team. That was firing pupdates
        for PRs the user wasn't personally tagged on, which the user
        considered noise. `user-review-requested:` filters to direct
        user tags only — team-level requests are filtered out.

        Also: `gh search prs --json` does NOT accept `headRefOid` (that
        field is only on `gh pr list` / `gh pr view` / graphql). We
        fetch core PR data, then enrich each hit with its head SHA via
        `gh pr view` so the dedup source_id can still include the SHA
        and re-requests on new commits aren't silently swallowed.
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
                # Best-effort. Missing SHA falls back to the plain
                # review/repo#N source_id path already in to_pupdates.
                pass
        return prs

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

        # For each of the user's PRs, check review and CI status + comments.
        # Open-PR comments feed the pr_review_commented pupdate (via
        # _comment_count / _latest_comment_at stashed on the pr dict);
        # they do NOT flow into Signals anymore — training data comes
        # from merged PRs only via _after_sync().
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

        return {
            "review_requests": review_requests,
            "my_prs": my_prs,
            "merged_prs": merged_prs,
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

        # Review requests -> high priority pupdates. source_id includes
        # the head SHA so a re-request on a new commit isn't dedup-swallowed
        # by the base poller (see base.py:162-167 note). Falls back to the
        # plain `review/repo#number` form if the SHA isn't present — keeps
        # behavior stable for gh versions that don't return headRefOid.
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
        """Intentionally no-op — training signals come from merged PRs.

        The earlier implementation emitted a fresh Signal for every
        review comment on every OPEN PR, every poll, with no dedup.
        Since the same comments appeared in raw_data every 5-minute
        tick for as long as a PR was open, the Signal table filled
        with exact duplicates indefinitely.

        Training data should come from feedback the team actually
        acted on — i.e. comments that survived to merge. `_after_sync()`
        handles that: it scrapes inline comments from merged PRs and
        dedupes them by (text, file_path, diff_hunk) against existing
        Signal rows for the repo.
        """
        return []

    def _after_sync(self, raw_data, db_session):
        """For each merged PR in this poll, fetch inline review comments
        and create unsynthesized Signal rows with code_context. Dedupes
        per-repo against existing signals by external_id or
        (file_path, diff_hunk) for legacy rows.

        Three phases — read existing dedup keys, do all network calls
        with no DB activity, then a single fast write batch + commit.
        Earlier the read / network / write were interleaved, which
        held the SQLite write lock across slow per-PR API calls and
        blocked every other writer in the process.

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

        # PHASE 1: read existing dedup keys per repo + check which PRs
        # we've already scraped. Skipping already-scraped PRs is the
        # difference between "scrape every PR every 5min forever" and
        # "scrape each PR once after merge" — the merged-PR list
        # itself has no time cursor (gh pr list returns the latest 5
        # merged regardless of when), so we use the pr_merged Pupdate
        # row as the cursor: if it carries comments_scraped_at on its
        # extra, we've already pulled comments for that PR.
        from planet_maiko.models.pupdate import Pupdate

        existing_per_repo = {}
        scraped_pupdate_by_pr = {}  # (repo, number) -> Pupdate row
        for repo in by_repo:
            existing_ids = set()
            existing_legacy = set()  # (file_path, diff_hunk)
            existing_text_keys = set()  # (file_path, body[:120]) — last-resort
            for s in Signal.query.filter_by(
                repo=repo, source_type="pr_comment"
            ).all():
                if s.external_id:
                    existing_ids.add(s.external_id)
                if s.file_path and s.code_context:
                    existing_legacy.add((s.file_path, s.code_context))
                # Text-based fallback for legacy rows missing both
                # external_id and code_context. Synthesis mutates
                # signal.text but original_text stays raw, so prefer
                # that when present.
                raw = (s.original_text or s.text or "")[:120]
                if s.file_path and raw:
                    existing_text_keys.add((s.file_path, raw))
            existing_per_repo[repo] = (
                existing_ids, existing_legacy, existing_text_keys,
            )

            # Look up the pr_merged pupdates for the PRs we'd otherwise
            # scrape this poll. The id is deterministic from the
            # source_id pattern this poller uses for merge events.
            for pr in by_repo[repo]:
                number = pr.get("number")
                if not number:
                    continue
                source_id = f"merged/{repo}#{number}"
                pup_id = self.generate_id(source_id)
                pup = db_session.get(Pupdate, pup_id)
                if pup:
                    scraped_pupdate_by_pr[(repo, number)] = pup
        db_session.rollback()

        # PHASE 2: network calls only. Build a list of Signal kwargs;
        # no DB activity in this loop. Per-PR API calls can run
        # several seconds total — keeping them out of the write tx
        # is the whole point of this rewrite.
        new_rows = []
        scraped_now = []  # PRs we successfully pulled this poll
        for repo, prs in by_repo.items():
            existing_ids, existing_legacy, existing_text_keys = existing_per_repo[repo]
            for pr in prs:
                number = pr.get("number")
                if not number:
                    continue
                pup = scraped_pupdate_by_pr.get((repo, number))
                if pup and (pup.extra or {}).get("comments_scraped_at"):
                    # Already pulled this PR's comments on a prior
                    # poll. Merged PRs are typically frozen — re-scraping
                    # is pure waste.
                    continue
                comments = fetch_comments_for_pr(repo, number)
                scraped_now.append((repo, number))
                for entry in comments:
                    external_id = entry.get("id") or None
                    body = entry["body"][:500]
                    file_path = entry.get("path") or None
                    diff_hunk = entry.get("diff_hunk") or None

                    # Check ALL three dedup keys regardless of which
                    # one the new entry carries. The earlier code
                    # gated the legacy check on `not external_id`,
                    # so a re-scrape that now has external_id
                    # bypassed the (path, hunk) match against an
                    # older row that only had legacy keys.
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
                    # Update local dedup sets so duplicate comments
                    # within this same poll don't double-insert.
                    if external_id:
                        existing_ids.add(external_id)
                    if file_path and diff_hunk:
                        existing_legacy.add((file_path, diff_hunk))
                    if file_path:
                        existing_text_keys.add((file_path, body[:120]))

        # PHASE 3: write batch + cursor flag, single commit. The
        # comments_scraped_at flag goes on the pr_merged Pupdate so
        # the next poll skips this PR up front. Without the flag,
        # a PR that yields zero new signals (already deduped, or no
        # inline comments) would re-trigger the API call every time.
        if not new_rows and not scraped_now:
            return

        from datetime import datetime as _dt, timezone as _tz
        scraped_at = _dt.now(_tz.utc).isoformat()

        for kwargs in new_rows:
            db_session.add(Signal(**kwargs))

        for (repo, number) in scraped_now:
            pup = scraped_pupdate_by_pr.get((repo, number))
            if pup is None:
                # The pr_merged pupdate is created in the same run by
                # to_pupdates(); it should be in the DB by now since
                # base.run() commits pupdates before _after_sync. If
                # we still can't find it, skip the flag — next poll's
                # lookup will pick it up.
                pup_id = self.generate_id(f"merged/{repo}#{number}")
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
