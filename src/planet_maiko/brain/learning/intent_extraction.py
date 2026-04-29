"""Generate Claude-authored "violation pattern" descriptions for each
graduated rule, grounded in the team's actual PR-comment history.

The output of this module — `Learning.violation_description` — is the
text that gets embedded for RAG retrieval. The richer this description
is (specific to the team's libraries, idioms, and code patterns), the
better retrieval works.

Pipeline per rule:

  1. Gather 5-8 representative signals tied to this Learning, with
     their diff_hunk and reviewer comment text.
  2. Build a prompt that includes the rule + each piece of evidence.
  3. Send to Claude (Haiku is cheap and good enough for this task).
  4. Parse the response into a clean description string.
  5. Caller embeds it and stores both on the Learning.

Cost estimate: ~3-5K input tokens × 200 output tokens × Haiku rates
≈ $0.001 per rule. 300 rules ≈ $0.30 one-time backfill.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


# Token budget: keep total prompt input ~5K tokens to fit comfortably
# inside Haiku's context with room for the response. We approximate
# tokens as chars/4 for budget enforcement.
MAX_PROMPT_CHARS = 20_000
MAX_HUNK_CHARS_PER_EXAMPLE = 1_200
MAX_COMMENT_CHARS = 600
DEFAULT_EXAMPLES_PER_RULE = 6


_VIOLATION_PROMPT = """You're describing the code-violation pattern for a team's coding rule, grounded in actual examples from their PR review history. The output will be embedded and used to retrieve this rule when reviewing similar new code, so the description should capture WHAT VIOLATIONS LOOK LIKE in this team's codebase.

## The rule

Rule: {rule_text}
Category: {category}
{scope_line}
Signal count: {n_signals} graduated from PR comments

## Historical evidence

Below are real code samples from this team's PRs that were flagged as violating this rule. Each includes the reviewer's actual comment so you can see what specifically caught their attention.

{evidence_blocks}

## Your task

Synthesize a violation pattern description that captures what code looks like when it violates this rule in THIS team's codebase. The description should be:

1. GROUNDED in the historical examples — reference team-specific module paths, internal libraries, or framework idioms where they recur in the evidence
2. GENERAL enough to recognize variations the team hasn't yet seen
3. SPECIFIC enough that a reviewer reading the description could spot a violation in unfamiliar code

Cover:
- STRUCTURAL PATTERNS — code shapes that repeatedly trigger this rule
- COMMON CONTEXTS — where in the codebase this typically happens
- TELLTALE SIGNS — what's present (or notably absent) in violations
- VARIATIONS — different forms the violation takes across the team's code

Length: 4-6 sentences. Genericize variable/function names, but DO reference team-specific module paths or library names if they show up consistently in the evidence.

Output ONLY the description, no preamble or formatting.
"""


def _hunk_for_signal(signal):
    """Pick the most informative diff hunk for a signal. Prefers the
    first hunk in `examples` when present (richer per-row metadata),
    falls back to `code_context` (older row format)."""
    if signal.examples:
        for ex in signal.examples:
            hunk = (ex.get("diff_hunk") or "").strip()
            if hunk:
                return ex.get("path") or signal.file_path or "", hunk
    if signal.code_context:
        return signal.file_path or "", signal.code_context.strip()
    return signal.file_path or "", ""


def _truncate(text, max_chars):
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    # Truncate at a line boundary near max_chars when possible.
    cut = text[:max_chars]
    last_newline = cut.rfind("\n")
    if last_newline > max_chars - 200:
        cut = cut[:last_newline]
    return cut + "\n…(truncated)"


def gather_evidence_for_learning(learning, n_examples=DEFAULT_EXAMPLES_PER_RULE):
    """Pick a small representative set of Signals to use as evidence
    for this learning's violation description.

    Heuristic priority: most recent signals first, with rough
    deduplication on the diff_hunk (don't include 5 nearly-identical
    examples). Returns a list of dicts with the data the prompt
    actually needs — keeps the prompt-builder simple and avoids
    leaking ORM objects into formatting code.
    """
    from planet_maiko.models.signal import Signal

    signals = (
        Signal.query
        .filter_by(learning_id=learning.id)
        .order_by(Signal.created_at.desc())
        .all()
    )

    seen_prefixes = set()
    evidence = []
    for s in signals:
        if len(evidence) >= n_examples:
            break
        path, hunk = _hunk_for_signal(s)
        if not hunk:
            continue
        # Cheap dedup: collapse on the first ~100 chars of the hunk.
        prefix = hunk[:120].strip()
        if prefix in seen_prefixes:
            continue
        seen_prefixes.add(prefix)

        evidence.append({
            "path": path or "",
            "diff_hunk": _truncate(hunk, MAX_HUNK_CHARS_PER_EXAMPLE),
            "reviewer_text": _truncate(
                s.original_text or s.text or "",
                MAX_COMMENT_CHARS,
            ),
            "pr_title": "",  # placeholder — Signal doesn't carry PR title yet
            "repo": s.repo or "",
        })

    return evidence


def build_violation_prompt(learning, evidence):
    """Format the prompt for Claude. Truncates the whole thing if it
    would exceed MAX_PROMPT_CHARS — better to drop a couple of examples
    than overflow Haiku's context."""
    scope_line = ""
    if learning.scope_repo:
        scope_line = f"Scope: applies in {learning.scope_repo}"
    elif learning.is_global:
        scope_line = "Scope: global (applies across repos)"

    blocks = []
    for i, ev in enumerate(evidence, 1):
        block_lines = [f"[Example {i}]"]
        if ev.get("repo"):
            block_lines.append(f"Repo: {ev['repo']}")
        if ev.get("path"):
            block_lines.append(f"File: {ev['path']}")
        if ev.get("reviewer_text"):
            block_lines.append("Reviewer said:")
            block_lines.append(f"> {ev['reviewer_text']}")
        block_lines.append("Code that triggered the comment:")
        block_lines.append("```")
        block_lines.append(ev["diff_hunk"])
        block_lines.append("```")
        blocks.append("\n".join(block_lines))

    evidence_blocks = "\n\n".join(blocks) if blocks else "(no evidence available)"

    prompt = _VIOLATION_PROMPT.format(
        rule_text=learning.rule,
        category=learning.category,
        scope_line=scope_line,
        n_signals=learning.signal_count or 0,
        evidence_blocks=evidence_blocks,
    )

    # Drop trailing examples if we're over budget — keeps the prompt
    # safe for any provider's context limit and shaves token cost.
    while len(prompt) > MAX_PROMPT_CHARS and len(blocks) > 1:
        blocks.pop()
        evidence_blocks = "\n\n".join(blocks)
        prompt = _VIOLATION_PROMPT.format(
            rule_text=learning.rule,
            category=learning.category,
            scope_line=scope_line,
            n_signals=learning.signal_count or 0,
            evidence_blocks=evidence_blocks,
        )

    return prompt, len(blocks)


def generate_violation_description(learning):
    """Run the full pipeline for one Learning. Returns the generated
    description text on success, or None on any failure (LLM unavailable,
    no evidence, parse error). Caller is responsible for embedding and
    persisting; this function is pure compute.
    """
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model

    evidence = gather_evidence_for_learning(learning)
    if not evidence:
        # No graduated signals to ground on — skip rather than asking
        # Claude to hallucinate. We could fall back to a rule-text-only
        # prompt, but those descriptions are weaker; better to wait for
        # signals to accumulate.
        logger.info(
            f"[intent] Learning #{learning.id} has no evidence yet — skipping description"
        )
        return None

    prompt, used_examples = build_violation_prompt(learning, evidence)

    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        logger.warning("[intent] LLM runtime not available — skipping description")
        return None

    try:
        result = runtime.send(
            prompt,
            timeout=90,
            model=resolve_model("classify"),  # Haiku-tier; cheap and good enough
        )
    except Exception as e:
        logger.warning(f"[intent] LLM call failed for Learning #{learning.id}: {e}")
        return None

    if not result or not result.get("success"):
        logger.warning(f"[intent] LLM returned non-success for Learning #{learning.id}")
        return None

    text = (result.get("output") or "").strip()
    if not text:
        return None

    # Strip any stray code-fence wrapping.
    if text.startswith("```"):
        # Cut the opening fence + optional language tag, then the closing fence.
        text = text.lstrip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        if text.rstrip().endswith("```"):
            text = text.rstrip().rstrip("`").rstrip()
    text = text.strip().strip('"').strip("'").strip()

    if len(text) < 30:
        # Suspiciously short — something probably went wrong.
        logger.warning(
            f"[intent] Learning #{learning.id}: description too short ({len(text)} chars), skipping"
        )
        return None

    logger.info(
        f"[intent] Learning #{learning.id}: generated description from "
        f"{used_examples} examples ({len(text)} chars)"
    )
    return text


_DIFF_INTENT_PROMPT = """Describe the intent of this code change at the level of structure and shape, not implementation details.

Diff:
```
{diff}
```

Cover:
- WHAT KIND of change (add / remove / modify / refactor / behavior change)
- WHAT KIND of code is touched (validation / auth / data flow / API / test / config / etc.)
- The STRUCTURAL pattern of the change (e.g. "removes input validation while leaving the call site", "adds a new function but no tests", "refactors error handling to swallow exceptions")
- Any obvious risk signals visible in the diff (orphaned references, missing tests, new external dependencies, removed safety checks)

Length: 3-5 sentences. Genericize variable/function names — describe the shape and intent, not the specifics. Output ONLY the description, no preamble.
"""


def generate_diff_description(diff_text):
    """Ask Claude/Haiku to describe the intent of a diff in natural
    language, suitable for embedding alongside rule violation
    descriptions.

    Used as the optional `describe_diff=True` path in retrieval —
    matches diff-intent against rule-violation-pattern in the same
    natural-language space, which usually retrieves more accurately
    than embedding raw code text. Costs one Haiku call (~$0.001) per
    review when enabled.

    Returns the description string on success, or None on any failure
    (LLM unavailable, parse error). Caller falls back to embedding
    the raw diff text when None.
    """
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model

    if not diff_text or not diff_text.strip():
        return None

    # Truncate the diff so the prompt stays within Haiku context.
    # 12K chars is generous for most PR-sized diffs.
    truncated_diff = _truncate(diff_text, 12_000)
    prompt = _DIFF_INTENT_PROMPT.format(diff=truncated_diff)

    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        return None

    try:
        result = runtime.send(
            prompt,
            timeout=60,
            model=resolve_model("classify"),
        )
    except Exception as e:
        logger.debug(f"[intent] generate_diff_description: LLM call failed: {e}")
        return None

    if not result or not result.get("success"):
        return None

    text = (result.get("output") or "").strip()
    if text.startswith("```"):
        text = text.lstrip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        if text.rstrip().endswith("```"):
            text = text.rstrip().rstrip("`").rstrip()
    text = text.strip().strip('"').strip("'").strip()

    if len(text) < 30:
        return None
    return text
