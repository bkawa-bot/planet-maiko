"""Rule-level RAG retrieval: given a code diff, surface the team's
graduated rules whose violation patterns most closely match it.

This replaces the "shove all 300 rules into Claude's context" approach
with focused retrieval — Claude only sees the K rules likely relevant
to *this specific* diff. Faster, cheaper, less attention dilution, and
actually scales as the rule corpus grows.

Pipeline at review time:
  1. Embed the new diff (or its intent description, if richer signal
     is wanted — controlled by `describe_diff` flag)
  2. Cosine-match against every active Learning's violation_embedding
  3. Filter by repo scope (rules tied to this repo + globals)
  4. Return top-K Learnings with their similarity scores

Most callers want `find_relevant_rules(diff, repo, k=5)`. The
underlying scoring is exposed in `score_rules_for_diff()` for finer
control (e.g. UIs that want to show all rules with their scores, not
just the top K).
"""

import logging

logger = logging.getLogger(__name__)

# Below this similarity, the rule isn't really relevant — exclude
# from results to avoid surfacing noise. Tuned conservatively;
# rule-level retrieval typically lands in 0.5-0.85 range for real
# matches, so 0.40 catches "weakly relevant" without exposing junk.
DEFAULT_MIN_SIMILARITY = 0.40


def _learning_in_scope(learning, repo):
    """True if the learning applies to the given repo. None repo means
    'no scope filter — include everything'. When repo is set, include
    rules scoped to that repo PLUS globals (no scope_repo or is_global)."""
    if repo is None:
        return True
    if learning.is_global:
        return True
    if learning.scope_repo is None:
        return True  # treat unscoped rules as implicit globals
    return learning.scope_repo == repo


def score_rules_for_diff(diff, repo=None):
    """Score every active Learning's violation pattern against the
    diff. Returns a list of (learning, score) tuples sorted descending
    by score. Doesn't filter by score threshold — caller decides what
    to do with low scores.

    Always runs the diff through Claude/Haiku first to extract a
    natural-language intent description, then embeds THAT for the
    cosine match. This puts both sides (rule violation descriptions
    and diff intent description) in the same natural-language space —
    retrieves dramatically better than embedding raw code. Costs
    ~$0.001 + 1-2s per call.

    Falls back to embedding the raw diff text when the diff-description
    LLM call fails (runtime down, timeout, parse error). Retrieval
    quality is lower in that fallback but the system stays functional
    rather than returning empty results.
    """
    from planet_maiko.models.learning import Learning
    from planet_maiko.brain.learning.embeddings import (
        embed_text,
        cosine_similarity,
    )
    from planet_maiko.brain.learning.intent_extraction import (
        generate_diff_description,
    )

    query_text = diff
    try:
        described = generate_diff_description(diff)
        if described:
            query_text = described
    except Exception as e:
        logger.debug(f"[retrieval] diff-description failed, falling back to raw diff: {e}")

    query_vec = embed_text(query_text)
    if query_vec is None:
        logger.warning("[retrieval] embedding backend unavailable — returning empty result")
        return []

    candidates = (
        Learning.query
        .filter_by(status="active")
        .filter(Learning.violation_embedding.isnot(None))
        .all()
    )

    scored = []
    for learning in candidates:
        if not _learning_in_scope(learning, repo):
            continue
        if not learning.violation_embedding:
            continue
        sim = cosine_similarity(query_vec, learning.violation_embedding)
        scored.append((learning, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def find_relevant_rules(diff, repo=None, k=5,
                        min_similarity=DEFAULT_MIN_SIMILARITY):
    """Top-K rules whose violation patterns best match the diff.

    Returns a list of dicts (not Learning ORM objects) so callers can
    serialize freely:

        [{"learning": Learning, "score": 0.78}, ...]

    Filters out anything below `min_similarity` first — better to return
    fewer high-quality matches than to surface noise. If after filtering
    fewer than K remain, that's fine — just return what we have.
    """
    scored = score_rules_for_diff(diff, repo=repo)
    relevant = [(l, s) for l, s in scored if s >= min_similarity]
    top = relevant[:k]
    return [{"learning": l, "score": s} for l, s in top]


def format_rules_for_prompt(rules):
    """Render a top-K list as a single chunk of text suitable for
    injecting into Claude's review prompt. Format is intentionally
    simple — Claude doesn't need rich markup, it just needs the rule
    text and which category it's in."""
    if not rules:
        return "(no relevant rules surfaced)"
    lines = []
    for i, item in enumerate(rules, 1):
        l = item["learning"]
        score = item["score"]
        lines.append(f"{i}. [{l.category}] {l.rule}")
        if l.violation_description:
            lines.append(f"   Pattern: {l.violation_description}")
        lines.append(f"   (relevance: {score:.2f})")
    return "\n".join(lines)
