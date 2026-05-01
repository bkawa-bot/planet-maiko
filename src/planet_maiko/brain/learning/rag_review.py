"""End-to-end RAG review: retrieve top-K rules → Claude reviews diff
against them → returns a structured review.

This is the "last mile" that ties together the rule extraction
pipeline (signals → learnings → graduated rules with embeddings)
and the actual reviewer flow. Most callers want `review_with_rag()`.

Pipeline per call:

  1. retrieve top-K rules whose scenario descriptions match the
     diff (via rule_retrieval.find_relevant_rules — itself uses
     the multi-granularity diff decomposition)
  2. build a Claude prompt with the diff + each rule's rule text +
     scenario description
  3. send to Claude (Sonnet-tier — the actual review needs more
     reasoning than the indexing-time descriptions did)
  4. return the review text + the rules used so the caller can
     show which rules were considered

Cost per review: ~$0.002 Haiku (diff decomposition) + free
embeddings + ~$0.01-0.05 Sonnet (the review itself, depending on
how much code is in scope). Way cheaper than dumping all 300
rules into Claude every time.
"""

import logging

logger = logging.getLogger(__name__)


# How many rules we'll surface to Claude. Higher = more chances to
# catch something, but dilutes the prompt and risks Claude over-
# flagging. 5 is the sweet spot — typically 2-3 are genuinely
# relevant + 2-3 close-but-not-quite that Claude can reasonably
# dismiss.
DEFAULT_TOP_K = 5

# Below this similarity, the rule isn't really relevant — better
# to surface 3 strong matches than dilute the prompt with 5
# including 2 weak ones. Tuned conservatively; adjust per how the
# top-K is actually feeling in real reviews.
DEFAULT_MIN_SIMILARITY = 0.45


_REVIEW_PROMPT = """You are reviewing a code change against your team's coding rules. The system has retrieved the rules whose typical scenarios most closely match this change — but it's up to you to decide whether each rule actually applies and whether it's actually being violated.

## Diff being reviewed

```
{diff}
```

## Candidate rules (retrieved by scenario similarity)

{rules_block}

## Your task

For each rule above, decide:

1. Does this rule's scenario actually apply to the diff? (The retrieval was based on similarity, not certainty — sometimes the closest match still isn't relevant.)
2. If it applies, is the diff violating it? Or following it correctly?
3. If violating, point at the specific code (file/lines or the construct) and explain how.

Output format:

For each rule that's both applicable AND violated:

VIOLATION: [{{category}}] {{rule}}
  - What: {{specific code construct or line}}
  - Why: {{how it violates the rule}}
  - Fix: {{what would address it}}

For each rule that's applicable but the code follows it correctly:

OK: [{{category}}] {{rule}}
  - Note: {{brief — confirms the rule was checked, why this code is fine}}

For each rule that doesn't actually apply to this diff:

SKIPPED: [{{category}}] {{rule}}
  - Why: {{one sentence — why this rule's scenario doesn't apply here}}

## Propose new rules (only when warranted)

If — and ONLY if — you spot something genuinely flag-worthy that NONE of the retrieved rules above (even sort-of) cover, emit a PATTERN block per finding. These become proposed Signals that the team reviews before they graduate into rules. Be conservative — quality over quantity.

Emit a PATTERN ONLY when ALL of:
  1. It's worth flagging across PRs, not a one-off nit specific to this diff.
  2. None of the retrieved rules above (applicable, OK, or skipped) covers it.
  3. It generalizes to other changes — a rule, not a personal preference.

When in doubt, don't emit. Skipping is the right default.

PATTERN format (use exactly this shape, lowercase keys, --- fences for the code block):

PATTERN: [category] Short, actionable rule (one sentence, imperative)
file: path/to/file.py
code:
---
<the diff hunk that exemplifies the issue — keep it tight, just the lines that show the pattern>
---

Valid categories: security, error_handling, testing, performance, api_design, architecture, null_safety, style, naming, docs, domain_knowledge, gotcha, team.

## Overall

End with a brief OVERALL summary (2-3 sentences) — the gist of what
this diff is doing, plus how many violations were found.

If the rules retrieved feel completely irrelevant to the diff, say so
in the OVERALL summary — sometimes the closest match still isn't
useful, and that's worth flagging.
"""


def _format_rule_for_prompt(rule_item):
    """Render one retrieved rule into the Claude prompt block."""
    learning = rule_item["learning"]
    score = rule_item.get("score", 0.0)
    lines = [
        f"### {learning.rule}",
        f"Category: {learning.category}",
        f"Retrieval score: {score:.2f}",
    ]
    if learning.violation_description:
        lines.append("Scenario this rule applies to:")
        lines.append(f"  {learning.violation_description}")
    return "\n".join(lines)


def review_with_rag(diff, repo=None, k=DEFAULT_TOP_K,
                    min_similarity=DEFAULT_MIN_SIMILARITY,
                    model_tier="skill:pr-review"):
    """Run a full RAG-backed review of a diff.

    Args:
        diff: the code change to review (raw diff text or just code).
        repo: optional repo name for filtering retrieved rules.
        k: max rules to surface to Claude.
        min_similarity: minimum cosine similarity for a rule to count
            as a candidate (filters out weakly-related rules).
        model_tier: routing key for resolve_model. Defaults to
            "skill:pr-review" (Sonnet-tier — review needs more
            reasoning than the indexing-time descriptions did).
            Pass "classify" to use Haiku-tier instead if cost matters
            more than reasoning depth.

    Returns:
        dict with:
          - success: bool
          - review: str (Claude's full review text), or error on failure
          - rules: list of {id, rule, category, score} that were
            surfaced — useful for the caller to display "rules
            considered" alongside the verdict
          - num_rules: int (how many rules were retrieved + included
            in the prompt)
          - error: str (only on failure)
    """
    from planet_maiko.brain.learning.rule_retrieval import find_relevant_rules
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model

    if not diff or not diff.strip():
        return {"success": False, "error": "diff is empty"}

    rule_items = find_relevant_rules(
        diff, repo=repo, k=k, min_similarity=min_similarity,
    )

    if not rule_items:
        # No rules surfaced — could mean retrieval found nothing
        # relevant (legitimately rare diff) or the embedding backend
        # is down. Caller can still show the diff with a "no rules
        # matched" note.
        return {
            "success": True,
            "review": "(no relevant team rules retrieved for this diff — nothing the team-knowledge layer would flag)",
            "rules": [],
            "num_rules": 0,
        }

    rules_block = "\n\n".join(_format_rule_for_prompt(r) for r in rule_items)
    prompt = _REVIEW_PROMPT.format(diff=diff, rules_block=rules_block)

    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        return {
            "success": False,
            "error": "LLM runtime is not available",
            "rules": [
                {
                    "id": r["learning"].id,
                    "rule": r["learning"].rule,
                    "category": r["learning"].category,
                    "score": round(r["score"], 4),
                }
                for r in rule_items
            ],
            "num_rules": len(rule_items),
        }

    try:
        result = runtime.send(
            prompt,
            timeout=180,
            model=resolve_model(model_tier),
        )
    except Exception as e:
        logger.warning(f"[rag-review] LLM call failed: {e}")
        return {
            "success": False,
            "error": f"LLM call failed: {e}",
            "num_rules": len(rule_items),
        }

    if not result or not result.get("success"):
        return {
            "success": False,
            "error": "LLM returned non-success",
            "num_rules": len(rule_items),
        }

    review_text = (result.get("output") or "").strip()
    if not review_text:
        return {
            "success": False,
            "error": "LLM returned empty review",
            "num_rules": len(rule_items),
        }

    # Parse PATTERN blocks Claude may have emitted for findings not
    # covered by any retrieved rule. They become Signals (status =
    # pending until the cluster pass + user approval) — same path as
    # PR-comment signals and worktree-agent PATTERNs. Cleaned text
    # is what we return to the caller; the raw text with blocks is
    # gone after parsing.
    from planet_maiko.brain.learning.agent_output import parse_and_apply_blocks
    from planet_maiko.database import db
    parsed = parse_and_apply_blocks(
        review_text,
        repo=repo,
        reviewer_name="rag_review",
    )
    if parsed["patterns_emitted"]:
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f"[rag-review] commit failed for proposed signals: {e}")

    return {
        "success": True,
        "review": parsed["cleaned_output"] or review_text,
        "rules": [
            {
                "id": r["learning"].id,
                "rule": r["learning"].rule,
                "category": r["learning"].category,
                "score": round(r["score"], 4),
            }
            for r in rule_items
        ],
        "num_rules": len(rule_items),
        "patterns_proposed": parsed["patterns_emitted"],
    }
