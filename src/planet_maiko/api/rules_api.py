"""Rule-level RAG retrieval API.

Exposes the rule-retrieval system over HTTP so the review path (and
external agents via MCP) can ask "which of the team's graduated rules
are most relevant to this code change?" without having to hand the
model all 300+ rules.

Endpoints:
  POST /api/rules/relevant   — top-K rules for a given diff
  POST /api/rules/regenerate-descriptions — kick off backfill manually
  GET  /api/rules/embedding-status — diagnostic: which backend, how
                                      many rules ready
"""

import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

rules_bp = Blueprint("rules", __name__)


@rules_bp.route("/rules/relevant", methods=["POST"])
def relevant_rules():
    """Return the team's graduated rules whose violation patterns are
    most relevant to the supplied diff.

    Body:
        diff (required, string): the code change to retrieve against.
            Can be a unified diff, a single file, or a hunk.
        repo (optional, string): "org/name" — filters to rules scoped
            to this repo plus globals. Omitted or null means no filter.
        k (optional, int, default 5): max rules to return.
        min_similarity (optional, float, default 0.40): cutoff for the
            cosine score below which rules are considered irrelevant
            and not returned.

    The diff is always run through Claude/Haiku first to extract a
    natural-language intent description, which is what gets embedded
    for the cosine match. Costs ~$0.001 + 1-2s per call but produces
    dramatically better retrieval than embedding raw code text.
    Falls back to raw-diff embedding if the LLM call fails.

    Response:
        {
          "rules": [
            {
              "id": 42,
              "rule": "Always sanitize user input before SQL queries",
              "category": "security",
              "score": 0.78,
              "violation_description": "...",
              "scope_repo": "acme/api",
              "is_global": false,
              "signal_count": 8
            },
            ...
          ],
          "model": "BAAI/bge-small-en-v1.5",
          "rules_indexed": 287,
          "rules_total_active": 312
        }

    Notes:
      - `rules_indexed` counts active rules with a violation_embedding.
        While the backfill is still running at startup, this number
        ramps up; once it equals `rules_total_active`, retrieval is at
        full coverage.
      - Returns an empty `rules` array (not an error) when the embedding
        backend is unavailable. Caller is expected to gracefully fall
        back to "no retrieval" mode.
    """
    from planet_maiko.brain.learning.rule_retrieval import find_relevant_rules
    from planet_maiko.brain.learning.embeddings import embedding_model_name
    from planet_maiko.models.learning import Learning

    data = request.get_json(silent=True) or {}
    diff = (data.get("diff") or "").strip()
    if not diff:
        return jsonify({"error": "diff is required"}), 400

    repo = data.get("repo") or None
    k = max(1, min(50, int(data.get("k", 5) or 5)))
    min_sim = float(data.get("min_similarity", 0.40) or 0.40)

    matches = find_relevant_rules(
        diff,
        repo=repo,
        k=k,
        min_similarity=min_sim,
    )

    rules_indexed = (
        Learning.query
        .filter_by(status="active")
        .filter(Learning.violation_embedding.isnot(None))
        .count()
    )
    rules_total_active = Learning.query.filter_by(status="active").count()

    out_rules = []
    for item in matches:
        l = item["learning"]
        out_rules.append({
            "id": l.id,
            "rule": l.rule,
            "category": l.category,
            "score": round(item["score"], 4),
            "violation_description": l.violation_description,
            "scope_repo": l.scope_repo,
            "is_global": bool(l.is_global),
            "signal_count": l.signal_count or 0,
        })

    return jsonify({
        "rules": out_rules,
        "model": embedding_model_name(),
        "rules_indexed": rules_indexed,
        "rules_total_active": rules_total_active,
    })


@rules_bp.route("/rules/regenerate-descriptions", methods=["POST"])
def regenerate_descriptions():
    """Kick off the violation_description backfill manually. Useful
    after editing rules in bulk, or to recover from a failed startup
    backfill. Runs in a background thread; returns immediately.

    Body:
      force (optional, bool, default false): when true, regenerate
        EVERY active learning's description — needed after prompt
        changes that invalidate existing content (e.g. when the
        prompt was reframed from violation patterns to scenarios).
        Costs ~$0.001 per rule, so use intentionally.
    """
    from flask import current_app
    from planet_maiko.brain.learning.violation_backfill import backfill_in_background

    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))
    backfill_in_background(current_app._get_current_object(), force=force)
    return jsonify({"status": "started", "force": force}), 202


@rules_bp.route("/rules/review", methods=["POST"])
def review_rag():
    """End-to-end RAG review: retrieve top-K rules for the diff, send
    them to Claude alongside the diff, return Claude's review.

    Body:
        diff (required, string): the code change to review.
        repo (optional, string): filter retrieved rules to this repo
            plus globals.
        k (optional, int, default 5): max rules to surface to Claude.
        min_similarity (optional, float, default 0.45): cosine
            threshold below which rules are filtered out before
            being sent to Claude.

    Response:
        {
          "success": true,
          "review": "VIOLATION: ... OK: ... OVERALL: ...",
          "rules": [{"id": 42, "rule": "...", "category": "security",
                     "score": 0.78}, ...],
          "num_rules": 5
        }
    """
    from planet_maiko.brain.learning.rag_review import review_with_rag

    data = request.get_json(silent=True) or {}
    diff = (data.get("diff") or "").strip()
    if not diff:
        return jsonify({"success": False, "error": "diff is required"}), 400

    repo = data.get("repo") or None
    k = max(1, min(20, int(data.get("k", 5) or 5)))
    min_sim = float(data.get("min_similarity", 0.45) or 0.45)

    result = review_with_rag(
        diff, repo=repo, k=k, min_similarity=min_sim,
    )

    status = 200 if result.get("success") else 500
    return jsonify(result), status


@rules_bp.route("/rules/embedding-status", methods=["GET"])
def embedding_status():
    """Diagnostic: which embedding backend is active, and how many
    rules are ready for retrieval. Useful for the UI to show
    "RAG ready: 287/312 rules indexed" while backfill is running."""
    from planet_maiko.brain.learning.embeddings import (
        embedding_model_name,
        _select_backend,
    )
    from planet_maiko.models.learning import Learning

    backend = _select_backend()
    rules_indexed = (
        Learning.query
        .filter_by(status="active")
        .filter(Learning.violation_embedding.isnot(None))
        .count()
    )
    rules_total_active = Learning.query.filter_by(status="active").count()

    return jsonify({
        "backend": backend,
        "model": embedding_model_name(),
        "rules_indexed": rules_indexed,
        "rules_total_active": rules_total_active,
        "ready_pct": (
            round(100 * rules_indexed / rules_total_active, 1)
            if rules_total_active else 0.0
        ),
    })
