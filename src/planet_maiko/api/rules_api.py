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
        describe_diff (optional, bool, default False): when True,
            generates an LLM description of the diff first and embeds
            that for retrieval (richer signal, adds an LLM call). When
            False, embeds the diff text directly (faster, cheaper).
        min_similarity (optional, float, default 0.40): cutoff for the
            cosine score below which rules are considered irrelevant
            and not returned.

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
    # describe_diff defaults to True — the natural-language-vs-natural-
    # language cosine match is meaningfully sharper than embedding raw
    # code. Set False explicitly on hot paths that need sub-second
    # latency (pre-commit hooks, etc).
    describe_diff = bool(data.get("describe_diff", True))
    min_sim = float(data.get("min_similarity", 0.40) or 0.40)

    matches = find_relevant_rules(
        diff,
        repo=repo,
        k=k,
        min_similarity=min_sim,
        describe_diff=describe_diff,
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
        "describe_diff": describe_diff,
    })


@rules_bp.route("/rules/regenerate-descriptions", methods=["POST"])
def regenerate_descriptions():
    """Kick off the violation_description backfill manually. Useful
    after editing rules in bulk, or to recover from a failed startup
    backfill. Runs in a background thread; returns immediately."""
    from flask import current_app
    from planet_maiko.brain.learning.violation_backfill import backfill_in_background
    backfill_in_background(current_app._get_current_object())
    return jsonify({"status": "started"}), 202


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
