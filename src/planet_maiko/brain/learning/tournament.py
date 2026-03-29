"""Tournament system — reinforcement learning for rule selection.

Uses already-merged PRs as ground truth. Runs the same task with
different rule subsets and uses LLM-as-judge to score which combo
produced the best output. Results feed back into compile_brief().

The "model" isn't weights — it's a rule selection policy learned
from tournament outcomes.
"""

import json
import logging
import random
import subprocess
from datetime import datetime, timezone

from planet_maiko.database import db
from planet_maiko.models.tournament import Tournament, TournamentEntry
from planet_maiko.models.learning import Learning

logger = logging.getLogger(__name__)

# Exploration: new rules get included in N tournaments regardless of score
EXPLORATION_TOURNAMENTS = 3


def run_tournament(repo, pr_number, app):
    """Run a tournament on a merged PR.

    Args:
        repo: "org/repo-name"
        pr_number: the PR number
        app: Flask app for context

    Returns:
        Tournament dict with results, or None on failure
    """
    with app.app_context():
        # Step 1: Fetch the PR diff
        pr_data = _fetch_pr(repo, pr_number)
        if not pr_data:
            return None

        # Step 2: Create tournament record
        tournament = Tournament(
            pr_repo=repo,
            pr_number=pr_number,
            pr_title=pr_data["title"],
            pr_diff_summary=pr_data["diff_summary"][:2000],
            task_description=pr_data["task"],
            status="running",
        )
        db.session.add(tournament)
        db.session.flush()

        # Step 3: Build strategies
        strategies = _build_strategies(repo)

        # Step 4: Run each strategy
        from planet_maiko.agents.runtimes.claude_code import ClaudeCodeRuntime
        runtime = ClaudeCodeRuntime()
        if not runtime.is_available():
            tournament.status = "failed"
            db.session.commit()
            return None

        for strategy_name, learning_ids in strategies.items():
            brief = _build_brief_from_ids(learning_ids)

            prompt = f"""You are writing code for this task.

{f"Follow these coding guidelines:{chr(10)}{brief}" if brief else ""}

## Task
{pr_data['task']}

Write ONLY the code changes. No explanation."""

            result = runtime.send(prompt, timeout=180)

            entry = TournamentEntry(
                tournament_id=tournament.id,
                strategy=strategy_name,
                learning_ids=learning_ids,
                output=result.get("output", "")[:5000] if result.get("success") else "",
            )
            db.session.add(entry)
            logger.info(f"[tournament] Strategy '{strategy_name}': {len(result.get('output', ''))} chars")

        db.session.flush()

        # Step 5: LLM-as-judge scoring
        entries = TournamentEntry.query.filter_by(tournament_id=tournament.id).all()
        _score_entries(runtime, entries, pr_data["diff_summary"])

        # Step 6: Record winner and update learning scores
        best = max(entries, key=lambda e: e.score or 0)
        tournament.winning_strategy = best.strategy
        tournament.status = "completed"
        tournament.completed_at = datetime.now(timezone.utc)

        # Update learning success rates based on winning combo
        _update_scores_from_tournament(entries)

        db.session.commit()
        logger.info(f"[tournament] Completed: winner='{best.strategy}' score={best.score}")

        return tournament.to_dict()


def _fetch_pr(repo, pr_number):
    """Fetch PR data via gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo,
             "--json", "title,body,files,additions,deletions"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"[tournament] Failed to fetch PR: {result.stderr}")
            return None

        data = json.loads(result.stdout)

        # Get the diff
        diff_result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--repo", repo],
            capture_output=True, text=True, timeout=30,
        )
        diff = diff_result.stdout[:3000] if diff_result.returncode == 0 else ""

        files = data.get("files", [])
        file_names = [f.get("path", f.get("filename", "")) for f in files] if isinstance(files, list) else []

        return {
            "title": data.get("title", ""),
            "body": data.get("body", ""),
            "diff_summary": diff,
            "files": file_names,
            "task": f"Make changes to the following files in {repo}: {', '.join(file_names[:10])}. "
                    f"The goal: {data.get('title', 'implement changes')}. "
                    f"{data.get('body', '')[:500]}",
        }
    except Exception as e:
        logger.error(f"[tournament] PR fetch error: {e}")
        return None


def _build_strategies(repo):
    """Build the 4 competition strategies."""
    all_learnings = Learning.query.filter_by(status="active").all()

    if not all_learnings:
        return {"no_rules": []}

    all_ids = [l.id for l in all_learnings]

    # Strategy 1: Top 5 by confidence for this repo
    scoped = [l for l in all_learnings if l.scope_repo is None or l.scope_repo == repo]
    scoped.sort(key=lambda l: -l.confidence)
    relevant_ids = [l.id for l in scoped[:5]]

    # Strategy 2: 5 random rules
    random_ids = random.sample(all_ids, min(5, len(all_ids)))

    # Strategy 3: All rules
    # Strategy 4: Exploration — include untested rules
    from planet_maiko.models.tournament import TournamentEntry
    tested_ids = set()
    for entry in TournamentEntry.query.all():
        for lid in (entry.learning_ids or []):
            tested_ids.add(lid)

    untested = [l.id for l in all_learnings if l.id not in tested_ids]
    explore_ids = (untested[:3] + relevant_ids[:2]) if untested else relevant_ids

    strategies = {
        "relevant_5": relevant_ids,
        "random_5": random_ids,
        "all": all_ids,
        "exploration": explore_ids,
    }

    return strategies


def _build_brief_from_ids(learning_ids):
    """Build a brief from specific learning IDs."""
    if not learning_ids:
        return ""
    learnings = Learning.query.filter(Learning.id.in_(learning_ids)).all()
    return "\n".join(f"- {l.rule}" for l in learnings)


def _score_entries(runtime, entries, actual_diff):
    """Use LLM-as-judge to score all entries against the actual PR."""
    outputs_text = ""
    for i, entry in enumerate(entries):
        preview = (entry.output or "")[:1000]
        outputs_text += f"\n--- Strategy {i+1}: {entry.strategy} ---\n{preview}\n"

    prompt = f"""You are judging a code competition. Multiple agents were asked to produce code for the same task.
Here is the ACTUAL merged code (ground truth):

{actual_diff[:2000]}

Here are the agents' outputs:
{outputs_text}

Score each strategy from 0 to 10 based on how closely their output matches the actual merged code.
Consider: correct approach, similar patterns, relevant imports, proper error handling.

Respond with JSON:
{{"scores": [{{"strategy": "strategy_name", "score": N, "reason": "brief reason"}}]}}"""

    result = runtime.send_json(prompt, timeout=120)

    if result.get("parsed") and "scores" in result["parsed"]:
        for score_data in result["parsed"]["scores"]:
            for entry in entries:
                if entry.strategy == score_data.get("strategy"):
                    entry.score = score_data.get("score", 0)
                    entry.judge_reasoning = score_data.get("reason", "")
                    break
    else:
        # Fallback: all get 5
        for entry in entries:
            entry.score = 5.0


def _update_scores_from_tournament(entries):
    """Update learning success rates based on tournament results.

    Rules in the winning strategy get a positive signal.
    Rules in losing strategies get a mild negative signal.
    """
    if not entries:
        return

    best_score = max(e.score or 0 for e in entries)
    if best_score == 0:
        return

    for entry in entries:
        score = entry.score or 0
        is_winner = score >= best_score * 0.9  # within 90% of best

        for lid in (entry.learning_ids or []):
            learning = db.session.get(Learning, lid)
            if not learning:
                continue

            if is_winner:
                # Winning combo: boost confidence slightly
                learning.confidence = min(1.0, learning.confidence + 0.02)
            else:
                # Losing combo: slight decrease (much smaller than boost)
                learning.confidence = max(0.0, learning.confidence - 0.005)


def get_tournament_scores(repo=None):
    """Get average tournament scores per learning for a repo.

    Used by compile_brief() to rank rules.

    Returns:
        dict: learning_id → {"avg_score": float, "tournament_count": int}
    """
    query = TournamentEntry.query.join(Tournament)
    if repo:
        query = query.filter(Tournament.pr_repo == repo)

    entries = query.filter(TournamentEntry.score.isnot(None)).all()

    scores = {}
    for entry in entries:
        for lid in (entry.learning_ids or []):
            if lid not in scores:
                scores[lid] = {"total_score": 0, "count": 0}
            scores[lid]["total_score"] += entry.score or 0
            scores[lid]["count"] += 1

    return {
        lid: {
            "avg_score": data["total_score"] / data["count"] / 10,  # normalize to 0-1
            "tournament_count": data["count"],
        }
        for lid, data in scores.items()
        if data["count"] > 0
    }
