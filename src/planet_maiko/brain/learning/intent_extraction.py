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


_DIFF_INTENT_PROMPT = """Break this code change down at TWO granularities. Both matter for matching against team rules:

**Intent** (1-3 entries): the high-level purpose of the change. What is the engineer accomplishing? Strategic-level rules — "smoke-test new endpoints", "validate user input", "log all API errors" — match at this level.

**Operations** (3-15 entries depending on diff size): the specific code constructs being introduced or modified. These are the patterns reviewers would scrutinize line-by-line. Tactical-level rules — "prefer Optional.orElse over Optional.get", "use streams instead of for-loops", "use parameterized queries", "always close Connections" — match at this level.

Both levels are needed. Many team coding rules are construct-specific (Java Optional handling, loop conventions, error-handling idioms) and would be missed if we only described the change at the strategic level.

Diff:
```
{diff}
```

For each entry, write an active-voice description ("Adding…", "Modifying…", "Replacing…", "Removing…", "Uses…", "Returns…").

Operations should be SMALL — one line of behavior per entry. Examples:
  - "Replaces a for-loop with stream().filter().map()"
  - "Uses Optional.get() on a value that may be empty"
  - "Adds a try-with-resources block around a Connection"
  - "Concatenates a string into a SQL query"
  - "Adds a new public method that returns a Map<String, Object>"
  - "Catches a generic Exception without rethrowing"

DO NOT in either level:
- Judge whether code is good or bad
- Flag risk signals or potential issues
- Reference specific variable/function names
- Editorialize

## Example 1: small focused diff

Diff: a 30-line patch that adds a new POST endpoint validating user input and writing to a database via a parameterized query.

Output:
{{
  "intent": [
    {{"description": "Adding a new POST endpoint that accepts user input and creates a resource."}}
  ],
  "operations": [
    {{"description": "Adds a route handler with a request body schema."}},
    {{"description": "Validates incoming string fields against a length constraint."}},
    {{"description": "Executes a parameterized INSERT query with bound parameters."}},
    {{"description": "Returns a JSON response with the created resource."}}
  ]
}}

## Example 2: medium PR with refactor

Diff: 200 lines — adds a new GET endpoint AND refactors a service class to use Java streams instead of for-loops.

Output:
{{
  "intent": [
    {{"description": "Adding a new GET endpoint that returns paginated results."}},
    {{"description": "Refactoring an existing service class to use functional-style iteration."}}
  ],
  "operations": [
    {{"description": "Adds a route handler with pagination parameters."}},
    {{"description": "Constructs a SELECT query with LIMIT and OFFSET."}},
    {{"description": "Replaces a for-loop with stream().filter().map()."}},
    {{"description": "Uses Optional.map() to transform a value."}},
    {{"description": "Replaces an explicit List<String> accumulator with Collectors.toList()."}},
    {{"description": "Removes a temporary mutable variable used in the old loop."}}
  ]
}}

## Example 3: large mixed PR

Diff: 600 lines — dep bump + new endpoint + DAO refactor + tests.

Output:
{{
  "intent": [
    {{"description": "Updating an external dependency version."}},
    {{"description": "Adding a new POST endpoint for user signup."}},
    {{"description": "Refactoring the user data access layer."}},
    {{"description": "Adding integration tests for the new endpoint."}}
  ],
  "operations": [
    {{"description": "Updates the version of a third-party library in the build configuration."}},
    {{"description": "Adjusts an import path to match the new library's package layout."}},
    {{"description": "Adds a route handler for a signup endpoint."}},
    {{"description": "Validates email and password fields against length and format constraints."}},
    {{"description": "Executes a parameterized INSERT into the users table."}},
    {{"description": "Consolidates duplicate query construction into a private helper method."}},
    {{"description": "Replaces direct cursor iteration with a result-set mapper."}},
    {{"description": "Adds @Test methods covering the new signup endpoint."}},
    {{"description": "Adds setup/teardown fixtures for a test database."}}
  ]
}}

PREFER FEWER INTENT ENTRIES when you're unsure (1-3 max). Operations can be more granular but stay focused — one fact per entry. Active voice everywhere. Don't reference specific identifiers from the diff.

Output ONLY the JSON object, no preamble or commentary.
"""


# Cap on operations to prevent runaway embedding cost on pathological
# diffs. If Claude over-splits despite the prompt, we trim to keep
# retrieval cost bounded.
MAX_INTENT_ENTRIES = 5
MAX_OPERATION_ENTRIES = 20


def _strip_json_fencing(text):
    """Remove a ```json ... ``` wrapper that some Claude responses add
    despite being told not to."""
    text = text.strip()
    if text.startswith("```"):
        # Drop the opening fence + optional language tag.
        text = text.lstrip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
        # Drop the closing fence.
        if text.rstrip().endswith("```"):
            text = text.rstrip().rstrip("`").rstrip()
    return text.strip()


def _extract_descriptions(parsed_obj):
    """Pull the flat list of description strings out of a parsed JSON
    object. Handles two shapes:
      - {"intent": [{"description": "..."}], "operations": [...]}
      - {"description": "..."}  (single-element fallback)
    Returns a list of non-empty stripped strings."""
    if not isinstance(parsed_obj, dict):
        return []

    descriptions = []

    # Single-object fallback (rare — happens when Claude misreads the
    # prompt and outputs one entry instead of the structured shape).
    if "description" in parsed_obj and isinstance(parsed_obj["description"], str):
        d = parsed_obj["description"].strip()
        if d:
            descriptions.append(d)

    for key, cap in (("intent", MAX_INTENT_ENTRIES), ("operations", MAX_OPERATION_ENTRIES)):
        entries = parsed_obj.get(key) or []
        if not isinstance(entries, list):
            continue
        for entry in entries[:cap]:
            if isinstance(entry, dict):
                d = (entry.get("description") or "").strip()
            elif isinstance(entry, str):
                d = entry.strip()
            else:
                continue
            if d:
                descriptions.append(d)

    return descriptions


def generate_diff_descriptions(diff_text):
    """Ask Claude to describe a diff at TWO granularities — high-level
    intent (1-3 entries) and tactical operations (3-15 entries). Both
    are returned as a flat list of natural-language strings, ready to
    be embedded individually for retrieval.

    Why two granularities: many team rules are construct-level
    ("prefer Optional.orElse over Optional.get") and would never
    surface against a high-level description like "refactoring the
    user service." Operations capture the line-level patterns those
    rules care about. Intent captures broader strategic rules that
    apply to "the kind of change" being made.

    Cost: one Haiku call (~$0.002 per review) + N+M local embeddings
    (free under sentence-transformers).

    Returns a list of strings on success, or empty list on any
    failure. Caller is expected to fall back to embedding raw diff
    text when the list is empty.
    """
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model

    if not diff_text or not diff_text.strip():
        return []

    truncated_diff = _truncate(diff_text, 12_000)
    prompt = _DIFF_INTENT_PROMPT.format(diff=truncated_diff)

    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        return []

    try:
        result = runtime.send(
            prompt,
            timeout=60,
            model=resolve_model("classify"),
        )
    except Exception as e:
        logger.debug(f"[intent] generate_diff_descriptions: LLM call failed: {e}")
        return []

    if not result or not result.get("success"):
        return []

    text = _strip_json_fencing(result.get("output") or "")
    if not text:
        return []

    # Locate the first balanced JSON object — Claude sometimes adds
    # preamble despite being told not to.
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        logger.debug("[intent] generate_diff_descriptions: no JSON object found in response")
        return []

    import json as _json
    try:
        parsed = _json.loads(text[start:end + 1])
    except _json.JSONDecodeError as e:
        logger.debug(f"[intent] generate_diff_descriptions: JSON parse failed: {e}")
        return []

    descriptions = _extract_descriptions(parsed)
    if descriptions:
        logger.info(
            f"[intent] diff broken into {len(descriptions)} units "
            f"(intent + operations combined)"
        )
    return descriptions


# Backwards-compat alias so existing imports keep working. Returns
# the FIRST description (typically the high-level intent) joined as
# a single string for callers that need string semantics. New callers
# should use generate_diff_descriptions().
def generate_diff_description(diff_text):
    descriptions = generate_diff_descriptions(diff_text)
    return descriptions[0] if descriptions else None
