import { useEffect, useState } from "react";
import { Sparkles } from "@icons";
import { api } from "../api/client";

/**
 * Token-usage audit widget. Surfaces today's spend, today's call
 * count, the 7-day total, and the top sources within the window.
 *
 * Only covers Maiko's INTERNAL LLM calls (home overview, maiko
 * chat, pack router, learning synthesis, etc.). Agent-session burn
 * is billed against the user's Claude Code session and lives in
 * Anthropic's logs, not here.
 */
function fmtTokens(n) {
  if (!n) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

function fmtUsd(n) {
  const v = Number(n) || 0;
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

export default function UsageWidget() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api.getUsage({ days: 7 })
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setData(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading || !data) return null;

  const today = data.today || {};
  const totals = data.totals || {};
  const todayTokens = (today.input_tokens || 0) + (today.output_tokens || 0);
  const topSources = (data.by_source || []).slice(0, 3);

  return (
    <div className="home-widget home-usage-widget">
      <div className="widget-header">
        <Sparkles size={12} /> Spend today
      </div>
      <div className="usage-today">
        <span className="usage-today-cost">{fmtUsd(today.total_cost_usd)}</span>
        <span className="usage-today-meta">
          {fmtTokens(todayTokens)} tokens · {today.count || 0} call{today.count === 1 ? "" : "s"}
        </span>
      </div>
      <div className="usage-window">
        Last 7 days: {fmtUsd(totals.total_cost_usd)} · {totals.count || 0} calls
      </div>
      {topSources.length > 0 && (
        <div className="usage-sources">
          <div className="usage-sources-label">Top sources</div>
          {topSources.map((s) => (
            <div key={s.source} className="usage-source-row">
              <span className="usage-source-name">{s.source}</span>
              <span className="usage-source-cost">{fmtUsd(s.total_cost_usd)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
