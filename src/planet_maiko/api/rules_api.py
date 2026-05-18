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
  GET  /api/rules/export    — dump active rules as JSON for teammates
  POST /api/rules/import    — import a previously-exported rules JSON
"""

import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

rules_bp = Blueprint("rules", __name__)


@rules_bp.route("/rules/relevant", methods=["POST"])
def relevant_rules():
    """Return the team's graduated rules whose violation patterns are
    most relevant to the supplied diff.

    Body (one of `diff` or `queries` is required):
        diff (string): the code change to retrieve against. Can be a
            unified diff, a single file, or a hunk. The diff is run
            through Claude/Haiku to extract multi-granularity scenario
            descriptions before embedding (~$0.001 + 1-2s per call).
            Falls back to raw-diff embedding if the LLM call fails.
        queries (list[string]): free-text descriptions used directly,
            no Haiku step. For callers that have full context (e.g.
            an agent in a worktree) and have decomposed the change
            themselves. Each query is embedded; rules score against
            the MAX similarity across all queries. Cheaper + sharper
            than the diff path when applicable.
        repo (optional, string): "org/name" — filters to rules scoped
            to this repo plus globals. Omitted or null means no filter.
        k (optional, int, default 5): max rules to return.
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
    raw_queries = data.get("queries") or []
    if not isinstance(raw_queries, list):
        raw_queries = []
    queries = [q.strip() for q in raw_queries if isinstance(q, str) and q.strip()]
    if not diff and not queries:
        return jsonify({"error": "diff or queries is required"}), 400

    repo = data.get("repo") or None
    k = max(1, min(50, int(data.get("k", 5) or 5)))
    min_sim = float(data.get("min_similarity", 0.40) or 0.40)

    if queries:
        matches = find_relevant_rules(
            queries=queries,
            repo=repo,
            k=k,
            min_similarity=min_sim,
        )
    else:
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


@rules_bp.route("/rules/export", methods=["GET"])
def export_rules():
    """Export the team's active rules as a JSON payload for sharing with
    a teammate. Skips embeddings — the receiver regenerates them locally
    (cheap, avoids embedding-model version drift).

    Query params:
      repo (optional): "org/name" — include rules scoped to this repo
        plus globals. Without it, exports every active rule.
    """
    from planet_maiko.models.learning import Learning
    from sqlalchemy import or_

    repo = (request.args.get("repo") or "").strip() or None

    query = Learning.query.filter_by(status="active")
    if repo:
        query = query.filter(or_(Learning.scope_repo == repo, Learning.is_global.is_(True)))

    rules = query.order_by(Learning.category, Learning.id).all()

    payload = {
        "schema_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_from_repo": repo,
        "rule_count": len(rules),
        "rules": [
            {
                "rule": r.rule,
                "category": r.category,
                "scope_repo": r.scope_repo,
                "scope_language": r.scope_language,
                "is_global": bool(r.is_global),
                "violation_description": r.violation_description,
                "signal_count": r.signal_count or 0,
            }
            for r in rules
        ],
    }
    return jsonify(payload)


@rules_bp.route("/rules/import", methods=["POST"])
def import_rules():
    """Import rules from a previously-exported JSON payload. New rules
    land as status='active' with source='imported'. Duplicates (same
    aggregation_key) are skipped, not overwritten. After insert, kicks
    off the violation backfill so embeddings populate for the new rows
    (and any descriptions are filled in if missing from the export).
    """
    from flask import current_app
    from planet_maiko.database import db
    from planet_maiko.models.learning import Learning
    from planet_maiko.brain.learning.violation_backfill import backfill_in_background

    data = request.get_json(silent=True) or {}
    schema_version = data.get("schema_version", 1)
    if schema_version != 1:
        return jsonify({"error": f"unsupported schema_version: {schema_version}"}), 400

    rules_in = data.get("rules") or []
    if not isinstance(rules_in, list):
        return jsonify({"error": "rules must be a list"}), 400

    imported = 0
    skipped_duplicate = 0
    errors = []

    for idx, r in enumerate(rules_in):
        if not isinstance(r, dict):
            errors.append(f"row {idx}: not an object")
            continue
        rule_text = (r.get("rule") or "").strip()
        category = (r.get("category") or "").strip()
        if not rule_text or not category:
            errors.append(f"row {idx}: missing rule or category")
            continue

        scope_repo = r.get("scope_repo") or None
        scope_language = r.get("scope_language") or None

        text_prefix = rule_text[:80].lower().strip()
        agg_key = f"{category}|{scope_repo or '_global'}|{scope_language or '_any'}|{text_prefix}"

        # Dedup on the normalized rule text + scope, NOT aggregation_key.
        # A locally-mined Learning derives aggregation_key from the
        # ORIGINATING SIGNAL's text, colon-joined (processor.py
        # _make_aggregation_key); recomputing it here from the rule text,
        # pipe-joined, never matched a mined row, so even byte-identical
        # rules imported as duplicates. Matching the rule text directly
        # is format-agnostic and actually catches exact dupes.
        norm_rule = rule_text.strip().lower()
        dup = (
            Learning.query
            .filter(Learning.category == category)
            .filter(Learning.scope_repo == scope_repo)
            .filter(Learning.scope_language == scope_language)
            .filter(db.func.lower(db.func.trim(Learning.rule)) == norm_rule)
            .first()
        )
        if dup:
            skipped_duplicate += 1
            continue

        learning = Learning(
            rule=rule_text,
            category=category,
            scope_repo=scope_repo,
            scope_language=scope_language,
            is_global=bool(r.get("is_global", False)),
            source="imported",
            status="active",
            signal_count=int(r.get("signal_count", 0) or 0),
            aggregation_key=agg_key,
            violation_description=r.get("violation_description"),
            # violation_embedding stays null — backfill regenerates locally.
        )
        db.session.add(learning)
        imported += 1

    db.session.commit()

    # Kick off the backfill so new rows get embeddings (and any missing
    # descriptions). Runs in a background thread; this endpoint returns
    # immediately.
    if imported:
        backfill_in_background(current_app._get_current_object(), force=False)

    return jsonify({
        "imported": imported,
        "skipped_duplicate": skipped_duplicate,
        "errors": errors,
    })


@rules_bp.route("/rules/embedding-status", methods=["GET"])
def embedding_status():
    """Diagnostic: which embedding backend is active, and how many
    rules are ready for retrieval. Useful for the UI to show
    "RAG ready: 287/312 rules indexed" while backfill is running."""
    from planet_maiko.brain.learning.embeddings import embedding_model_name
    from planet_maiko.models.learning import Learning

    # Single local backend now; model name is None when it couldn't
    # load (not installed / cache empty). Frontend treats falsy backend
    # as "RAG offline".
    model = embedding_model_name()
    backend = "sentence_transformers" if model else None
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
