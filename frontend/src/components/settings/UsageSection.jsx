import { useEffect, useState } from "react";
import { Sparkles } from "@icons";
import { api } from "../../api/client";

/**
 * Token-usage audit section in Settings. Surfaces today's spend on
 * Maiko's internal LLM calls (home overview, maiko chat, pack
 * router, learning synthesis, etc.) plus the 7-day total and the
 * top sources within the window.
 *
 * Doesn't cover agent-session burn — that's billed against the
 * user's interactive Claude Code session and lives in Anthropic's
 * logs, not here.
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

export default function UsageSection() {
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

  const today = data?.today || {};
  const totals = data?.totals || {};
  const todayTokens = (today.input_tokens || 0) + (today.output_tokens || 0);
  const topSources = (data?.by_source || []).slice(0, 5);

  return (
    <section className="settings-collapsible">
      <div className="collapsible-header" style={{ cursor: "default" }}>
        <span><Sparkles size={14} /> Token usage</span>
      </div>
      <div className="collapsible-body">
        <div className="setup-hint">
          Maiko's own LLM calls only (home overview, chat, pack router,
          learning synthesis). Agent-session burn is billed against
          your interactive Claude Code session and lives in Anthropic's
          logs, not here.
        </div>

        {loading && (
          <div className="usage-section-empty">Loading…</div>
        )}

        {!loading && !data && (
          <div className="usage-section-empty">Couldn't load usage data.</div>
        )}

        {!loading && data && (
          <>
            <div className="usage-section-grid">
              <div className="usage-section-stat">
                <div className="usage-section-stat-label">Today</div>
                <div className="usage-section-stat-value">{fmtUsd(today.total_cost_usd)}</div>
                <div className="usage-section-stat-meta">
                  {fmtTokens(todayTokens)} tokens · {today.count || 0} call{today.count === 1 ? "" : "s"}
                </div>
              </div>
              <div className="usage-section-stat">
                <div className="usage-section-stat-label">Last 7 days</div>
                <div className="usage-section-stat-value">{fmtUsd(totals.total_cost_usd)}</div>
                <div className="usage-section-stat-meta">
                  {fmtTokens((totals.input_tokens || 0) + (totals.output_tokens || 0))} tokens · {totals.count || 0} calls
                </div>
              </div>
            </div>

            {topSources.length > 0 && (
              <div className="usage-section-sources">
                <div className="usage-section-sources-label">Top sources (last 7 days)</div>
                {topSources.map((s) => {
                  const tokens = (s.input_tokens || 0) + (s.output_tokens || 0);
                  return (
                    <div key={s.source} className="usage-section-source-row">
                      <span className="usage-section-source-name">{s.source}</span>
                      <span className="usage-section-source-meta">
                        {fmtTokens(tokens)} tokens · {s.count} call{s.count === 1 ? "" : "s"}
                      </span>
                      <span className="usage-section-source-cost">{fmtUsd(s.total_cost_usd)}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
