"""Generate "scenario" descriptions for each graduated rule, grounded
in the team's actual PR-comment history.

Despite the column name `violation_description` (kept for migration
stability), the content these prompts produce is NOT a description of
what violations look like — it's a description of the SCENARIOS where
the rule applies. The kinds of code changes that should pull this rule
into a reviewer's attention.

Why this distinction matters: at review time, Claude describes the
diff it's looking at. If we asked Claude to describe violations and
matched on that, we'd only retrieve rules when Claude had ALREADY
spotted something off — which defeats the purpose of retrieval.
Instead, the diff description says "this adds a new public endpoint"
and we want to surface every rule whose scenario is "applies to new
endpoints." Claude reasons about whether the rule was actually
violated separately, given the rule and the diff together.

Pipeline per rule:

  1. Gather 5-8 representative signals tied to this Learning, with
     their diff_hunk and reviewer comment text.
  2. Build a prompt that asks Claude to extract the SCENARIO — what
     kind of code change typically triggers this rule's relevance,
     not what a violation looks like.
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


_VIOLATION_PROMPT = """You're describing the kind of code change this rule applies to. The output will be embedded and matched against descriptions of new diffs at review time — same grammatical voice on both sides means tighter cosine matches, so frame your description AS IF YOU WERE DESCRIBING A TYPICAL DIFF that triggers the rule.

Use active voice: "Adding…", "Modifying…", "Refactoring…", "Removing…". DO NOT use abstract framings like "Any code change that…" or "Code changes which…". The diff-side description (what we'll match against) reads like "Adding a new POST endpoint that accepts user input"; your description should read in the same shape.

CRITICAL: do NOT describe what a violation looks like. Do NOT describe red flags, anti-patterns, or "telltale signs of bad code." Describe the SITUATION an engineer is in when this rule becomes relevant — what kind of change they're making, what kind of code they're writing or modifying. Assume the engineer hasn't yet realized the rule applies; your description is what helps the system notice that the rule is relevant to their work. Claude reasons about whether the rule was actually violated separately, given the rule and the diff together.

## The rule

Rule: {rule_text}
Category: {category}
{scope_line}
Signal count: {n_signals} graduated from PR comments

## Historical evidence

Below are real code changes from this team's PRs where this rule was applied. Look at WHAT KIND OF CHANGE was being made — that's the scenario you're trying to capture. The reviewer's specific comment shows you the violation, but you're describing the *category of change* the rule applies to, not the violation itself.

{evidence_blocks}

## Your task

Describe the kind of diff that triggers this rule. Cover:

1. WHAT KIND of change is happening
   (adding a new feature, modifying a query, refactoring, removing code, etc.)
2. WHAT KIND of code is being created or modified
   (new endpoint, new database query, new public function, new test, new dependency, etc.)
3. The BROAD CATEGORY of work
   (data access, public API, auth, error handling, configuration, testing, observability, etc.)
4. VARIATIONS — different forms the relevant change can take

DO NOT cover:
- What a violation looks like
- What's "wrong" with bad code
- Specific anti-patterns or red flags
- Implementation-specific details (variable names, exact function calls, library specifics)

## Examples of GOOD descriptions (note the active voice)

For "Add smoke tests for new endpoints":
> "Adding a new public-facing API endpoint, route handler, or web-accessible function. Covers REST endpoints (GET/POST/PUT/DELETE), RPC procedures, GraphQL resolvers, and new URL routes. Also includes meaningful modifications to an existing endpoint's public contract — new path, new request shape, or new response shape."

For "Always validate input on user-facing endpoints":
> "Adding or modifying a function that receives external data — request bodies, query parameters, file uploads, form submissions, or message-queue payloads. Covers both new endpoints and changes to existing ones that introduce or alter input fields."

For "Use parameterized queries":
> "Writing or modifying a database query that incorporates variable data — function parameters, request values, computed values, or results from other queries. Covers INSERT, UPDATE, DELETE, and SELECT-with-WHERE statements equally, regardless of database engine."

Notice: every example starts with a verb ("Adding", "Writing", "Modifying"), reads like a description of a specific diff, and never mentions what bad code looks like.

Length: 2-4 sentences. Generic enough to apply across languages, frameworks, and team conventions. Don't reference specific identifiers from the evidence — describe the situational pattern, not the implementation.

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


_DIFF_INTENT_PROMPT = """Describe what KIND of code change this diff represents — the situational context. The output will be matched against rule descriptions (also written in active voice — "Adding…", "Modifying…") to find which team rules might apply to this change.

Use active voice: "Adding…", "Modifying…", "Refactoring…", "Removing…". The rule side uses the same voice; matching same-style text yields tighter cosine similarity than mixing voices.

CRITICAL: do not judge whether the code is good or bad, do not flag risk signals, do not call out missing tests or any potential issues. Just describe what kind of work is being done — what the engineer is making, modifying, or removing. The system surfaces relevant rules separately, given this scenario description.

Diff:
```
{diff}
```

Cover:
1. WHAT KIND of change (adding/removing/modifying/refactoring)
2. WHAT KIND of code is being touched (endpoint, query, validation, test, config, dependency, business logic, etc.)
3. The BROAD CATEGORY of work (data access, public API, auth, error handling, observability, etc.)
4. The CHANGE INTENT (what the engineer is trying to accomplish — describe at the situational level, not the implementation level)

DO NOT include:
- Whether the code looks good or bad
- "Risk signals," "red flags," "potential issues," missing tests, etc.
- Specific variable/function names or library calls
- Editorial commentary on the change

## Examples of GOOD diff descriptions

For a diff that adds a new POST endpoint with validation:
> "Adding a new public API endpoint that accepts user input and creates a resource. Includes a route handler, a request schema, and a database write."

For a diff that modifies a SQL query:
> "Modifying an existing database query to add additional filter conditions and return a new field. Changes both the query parameters and the result shape."

For a diff that refactors error handling:
> "Refactoring how errors are handled in an existing service module. Moves try/except blocks and changes how exceptions propagate to callers."

Notice: none of these say "looks fine" or "missing tests" or "risky." They just describe what's happening.

Length: 3-5 sentences. Generic enough to match any rule's scenario description. Don't reference specific identifiers from the diff.

Output ONLY the description, no preamble.
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
