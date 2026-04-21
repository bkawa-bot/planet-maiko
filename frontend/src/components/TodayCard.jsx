import { useEffect, useState } from "react";
import {
  CheckSquare, Flame, AlertCircle, Brain, BookOpen, Bot, ChevronDown, ChevronRight,
} from "lucide-react";
import { api } from "../api/client";
import { relativeTime } from "../utils/dates";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import "./TodayCard.css";


// Mirror of Home.jsx / AgentsInsightsTab.jsx — keep in sync.
const AVATAR_EMOJI = {
  shiba: "🐕", corgi: "🐶", husky: "🐺", poodle: "🐩", golden: "🦮",
  beagle: "🐕‍🦺", dalmatian: "🐾", samoyed: "☁️", akita: "🐕", pomeranian: "🧸",
  calico_cat: "🐱", tabby_cat: "🐈", black_cat: "🐈‍⬛",
  bunny: "🐰", hamster: "🐹", fox: "🦊",
};


/**
 * Today's activity digest — the "what did my pack do today" glance.
 *
 * Only renders when the day has had *some* activity. On a fresh day
 * with nothing yet, it quietly doesn't show up so it doesn't bulk out
 * Home with empty buckets.
 */
export default function TodayCard() {
  const defaultOrg = useDefaultOrg();
  const [today, setToday] = useState(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getToday().then((d) => { if (!cancelled) setToday(d); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  if (!today) return null;

  const counts = {
    tasks: (today.tasks_completed || []).length,
    agents: (today.agents_active || []).length,
    learnings: (today.learnings_harvested || []).length,
    insights: (today.insights_approved || []).length,
    autoInvestigations: (today.auto_investigations || []).length,
    incidents: (today.incidents_detected || []).length,
  };
  const totalActivity =
    counts.tasks + counts.learnings + counts.insights +
    counts.autoInvestigations + counts.incidents;

  // Quiet day — don't clutter Home with an empty rollup.
  if (totalActivity === 0) return null;

  return (
    <div className="home-card home-today-card">
      <button
        className="home-today-toggle"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="home-card-header home-today-header">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <CheckSquare size={14} /> Today
          <span className="home-today-summary">
            {counts.tasks > 0 && <span>{counts.tasks} done</span>}
            {counts.agents > 0 && <span>· {counts.agents} agents active</span>}
            {counts.incidents > 0 && <span>· {counts.incidents} incidents</span>}
            {counts.learnings > 0 && <span>· {counts.learnings} learnings</span>}
            {counts.insights > 0 && <span>· {counts.insights} insights</span>}
          </span>
        </div>
      </button>

      {expanded && (
        <div className="home-today-body">
          {counts.agents > 0 && (
            <Section icon={<Bot size={11} />} label="Agents who showed up today">
              <div className="home-today-agents">
                {(today.agents_active || []).map((a) => (
                  <span key={a.id} className="home-today-agent-chip" title={a.role}>
                    {AVATAR_EMOJI[a.avatar] || "🐾"} {a.display_name}
                  </span>
                ))}
              </div>
            </Section>
          )}

          {counts.tasks > 0 && (
            <Section icon={<CheckSquare size={11} />} label={`${counts.tasks} tasks completed`}>
              <ul className="home-today-list">
                {(today.tasks_completed || []).slice(0, 8).map((t) => (
                  <li key={t.id} className="home-today-item">
                    {t.url ? (
                      <a href={t.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                        {t.title}
                      </a>
                    ) : t.title}
                    <span className="home-today-item-meta">
                      {t.assigned_agent_id && ` · ${t.assigned_agent_id.replace(/^agent-/, "")}`}
                      {t.status === "cancelled" && " · cancelled"}
                    </span>
                  </li>
                ))}
                {counts.tasks > 8 && <li className="home-today-more">+ {counts.tasks - 8} more</li>}
              </ul>
            </Section>
          )}

          {counts.incidents > 0 && (
            <Section icon={<AlertCircle size={11} />} label={`${counts.incidents} incidents detected`}>
              <ul className="home-today-list">
                {(today.incidents_detected || []).map((p) => (
                  <li key={p.id} className="home-today-item">
                    {p.title}
                    {p.dismissed && <span className="home-today-item-meta"> · dismissed</span>}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {counts.autoInvestigations > 0 && (
            <Section icon={<Flame size={11} />} label={`${counts.autoInvestigations} auto-investigations`}>
              <ul className="home-today-list">
                {(today.auto_investigations || []).map((t) => (
                  <li key={t.id} className="home-today-item">
                    {t.title}
                    <span className="home-today-item-meta">
                      {" · "}{t.status}
                      {t.pattern.length > 0 && ` · ${t.pattern.join(" + ")}`}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {counts.learnings > 0 && (
            <Section icon={<Brain size={11} />} label={`${counts.learnings} learnings harvested`}>
              <ul className="home-today-list">
                {(today.learnings_harvested || []).map((l) => (
                  <li key={l.id} className="home-today-item">
                    {l.rule}
                    <span className="home-today-item-meta"> · {l.category}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {counts.insights > 0 && (
            <Section icon={<BookOpen size={11} />} label={`${counts.insights} insights added to the playbook`}>
              <ul className="home-today-list">
                {(today.insights_approved || []).map((i) => (
                  <li key={i.id} className="home-today-item">
                    {i.text}
                    {i.repo_scope && <span className="home-today-item-meta" title={i.repo_scope}> · {formatRepo(i.repo_scope, defaultOrg)}</span>}
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      )}
    </div>
  );
}


function Section({ icon, label, children }) {
  return (
    <div className="home-today-section">
      <div className="home-today-section-header">{icon} {label}</div>
      <div className="home-today-section-body">{children}</div>
    </div>
  );
}
