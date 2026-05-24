"""Token-usage aggregation endpoint.

Reads TokenUsage rows written by the runtime and returns summary
totals (lifetime-since-window, today, per source, per day) so the
Home widget and any future spend dashboard can render the audit
without re-aggregating client-side.

Only covers Maiko's INTERNAL LLM calls. Agent-session burn (the
headless claude inside worktrees) is billed against the user's
interactive Claude Code session and isn't tracked here.
"""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from planet_maiko.database import db
from planet_maiko.models.token_usage import TokenUsage

usage_bp = Blueprint("usage", __name__)


def _empty_totals():
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "total_cost_usd": 0.0,
        "count": 0,
    }


def _sum_rows(rows):
    out = _empty_totals()
    for r in rows:
        out["input_tokens"] += r.input_tokens or 0
        out["output_tokens"] += r.output_tokens or 0
        out["cache_read_tokens"] += r.cache_read_tokens or 0
        out["cache_creation_tokens"] += r.cache_creation_tokens or 0
        out["total_cost_usd"] += r.total_cost_usd or 0.0
        out["count"] += 1
    out["total_cost_usd"] = round(out["total_cost_usd"], 4)
    return out


@usage_bp.route("/usage", methods=["GET"])
def get_usage():
    """Token usage summary over a window.

    Query params:
      days   — window size, default 7, max 90
      source — optional substring filter

    Response shape:
      {
        since: ISO8601,
        totals: { input_tokens, output_tokens, cache_*, total_cost_usd, count },
        today:  { same shape },
        by_source: [{ source, ...totals }, ...],   # within the window
        by_day:    [{ day: "YYYY-MM-DD", ...totals }, ...]
      }
    """
    try:
        days = max(1, min(int(request.args.get("days", 7)), 90))
    except (TypeError, ValueError):
        days = 7
    source_filter = (request.args.get("source") or "").strip() or None

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).replace(microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    q = TokenUsage.query.filter(TokenUsage.timestamp >= since)
    if source_filter:
        q = q.filter(TokenUsage.source.ilike(f"%{source_filter}%"))
    rows = q.all()

    today_rows = [r for r in rows if r.timestamp and r.timestamp >= today_start]

    # Per-source aggregation
    by_source_map = {}
    for r in rows:
        src = r.source or "unknown"
        agg = by_source_map.setdefault(src, _empty_totals())
        agg["input_tokens"] += r.input_tokens or 0
        agg["output_tokens"] += r.output_tokens or 0
        agg["cache_read_tokens"] += r.cache_read_tokens or 0
        agg["cache_creation_tokens"] += r.cache_creation_tokens or 0
        agg["total_cost_usd"] += r.total_cost_usd or 0.0
        agg["count"] += 1
    by_source = []
    for src, agg in by_source_map.items():
        agg["total_cost_usd"] = round(agg["total_cost_usd"], 4)
        by_source.append({"source": src, **agg})
    by_source.sort(key=lambda x: x["total_cost_usd"], reverse=True)

    # Per-day aggregation (UTC days, simpler than user-local for the
    # audit use case — exact day boundaries don't matter for spotting
    # a 10x spike).
    by_day_map = {}
    for r in rows:
        if not r.timestamp:
            continue
        day = r.timestamp.strftime("%Y-%m-%d")
        agg = by_day_map.setdefault(day, _empty_totals())
        agg["input_tokens"] += r.input_tokens or 0
        agg["output_tokens"] += r.output_tokens or 0
        agg["cache_read_tokens"] += r.cache_read_tokens or 0
        agg["cache_creation_tokens"] += r.cache_creation_tokens or 0
        agg["total_cost_usd"] += r.total_cost_usd or 0.0
        agg["count"] += 1
    by_day = []
    for day in sorted(by_day_map.keys()):
        agg = by_day_map[day]
        agg["total_cost_usd"] = round(agg["total_cost_usd"], 4)
        by_day.append({"day": day, **agg})

    return jsonify({
        "since": since.isoformat(),
        "days": days,
        "totals": _sum_rows(rows),
        "today": _sum_rows(today_rows),
        "by_source": by_source,
        "by_day": by_day,
    })
