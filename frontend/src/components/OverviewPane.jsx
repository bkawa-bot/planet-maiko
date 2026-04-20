import { useEffect, useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { renderMarkdown } from "../utils/markdown";
import { relativeTime } from "../utils/dates";
import { Sunrise, RefreshCw, FileText, X, Loader } from "lucide-react";
import "./OverviewPane.css";

/**
 * The Home overview pane — the primary surface of Planet Maiko.
 *
 * Fetches a rolling LLM-generated overview from /api/home/overview and
 * renders it as greeting + summary prose + focus cards + needs-you
 * cards + alive status + custom add-on section. The LLM picks which
 * tasks/pupdates matter and provides its own copy for each; the
 * frontend is thin glue that wires the references back to real
 * navigation / modal targets.
 *
 * No props — fetches everything it needs. Home.jsx just drops it in
 * as the main-column content.
 */

// Pupdate → action resolver. Given the pupdate referenced in needs,
// returns where the primary button should go. Mirrors the logic that
// used to live in Home's waitingCta so action routing stays consistent
// with what the old "What needs you" card did.
// Map a pupdate to where its action button should go. Returns null
// when there's no useful destination — caller hides the button in
// that case instead of rendering "Open" that bounces back to Home.
function resolveAction(p) {
  if (!p) return null;
  const taskId = p.metadata?.task_id;
  if (p.type === "agent_plan_for_approval" && taskId) {
    return { label: "Review plan", to: `/tasks/${taskId}/plan` };
  }
  const tags = p.tags || [];
  // Review agents produce a diff + inline comments + verdict — route
  // straight to the diff page so the user sees everything in context.
  const isReviewAgent =
    p.type === "agent_ready_for_review" && tags.includes("review");
  if (isReviewAgent && taskId) {
    return { label: "Open review", to: `/tasks/${taskId}/review` };
  }
  // Investigation / cartographer output is a markdown document, not
  // a diff — keep the artifact-modal path for those.
  const isReportLike =
    p.type === "pr_review_complete" ||
    p.type === "investigation_complete" ||
    (p.type === "agent_ready_for_review" &&
      (tags.includes("investigation") || tags.includes("cartographer")));
  if (isReportLike) {
    return { label: "Read report", artifact: true };
  }
  if (p.type === "agent_ready_for_review" && taskId) {
    return { label: "Review diff", to: `/tasks/${taskId}/review` };
  }
  if (p.type === "agent_stuck" && taskId) {
    return { label: "Help out", to: `/tasks/${taskId}` };
  }
  if (p.type === "pr_review_requested" || p.type === "pr_changes_requested") {
    return p.url
      ? { label: p.type === "pr_review_requested" ? "Review PR" : "Revise", href: p.url }
      : null;
  }
  // Fall-through: unknown type, or known type missing the context it
  // needs to route (agent_stuck without a task_id, etc). Return null
  // so the caller renders a read-only card — better than an "Open"
  // button that lands the user back on the page they came from.
  return null;
}

export default function OverviewPane() {
  const [overview, setOverview] = useState(null);
  const [generatedAt, setGeneratedAt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [pupdates, setPupdates] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [showAllNeeds, setShowAllNeeds] = useState(false);
  const [artifactModal, setArtifactModal] = useState(null);
  const navigate = useNavigate();

  const pupdateById = useMemo(() => {
    const m = {};
    for (const p of pupdates) m[p.id] = p;
    return m;
  }, [pupdates]);

  const taskById = useMemo(() => {
    const m = {};
    for (const t of tasks) m[t.id] = t;
    return m;
  }, [tasks]);

  const fetchAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [overviewRes, pupRes, taskRes] = await Promise.all([
        api.getHomeOverview(),
        api.getPupdates(),
        api.getTasks(),
      ]);
      setOverview(overviewRes.overview);
      setGeneratedAt(overviewRes.generated_at);
      setPupdates(pupRes);
      setTasks(taskRes);
    } catch (err) {
      setError(err.message || "Overview unavailable");
    }
    setLoading(false);
  };

  const refresh = async () => {
    setRefreshing(true);
    try {
      const data = await api.refreshHomeOverview();
      setOverview(data.overview);
      setGeneratedAt(data.generated_at);
      showToast("Fresh overview ☀️", "normal");
    } catch (err) {
      showToast(err.message || "Couldn't refresh", "high");
    }
    setRefreshing(false);
  };

  const handleDismiss = async (pupdateId) => {
    try {
      await api.dismissPupdate(pupdateId);
      setPupdates((prev) => prev.filter((p) => p.id !== pupdateId));
      // Optimistic: drop from overview.needs locally so the row
      // disappears without waiting for a regeneration.
      if (overview?.needs) {
        setOverview({
          ...overview,
          needs: overview.needs.filter((n) => n.pupdate_id !== pupdateId),
        });
      }
    } catch (err) {
      showToast("Couldn't dismiss", "high");
    }
  };

  useEffect(() => { fetchAll(); }, []);

  if (loading) {
    return (
      <div className="overview-pane overview-loading">
        <Loader size={16} className="spin" />
        <span>Looking around…</span>
      </div>
    );
  }

  if (error && !overview) {
    return (
      <div className="overview-pane overview-error">
        <Sunrise size={24} />
        <p>Overview unavailable right now.</p>
        <button
          className="btn btn-primary"
          onClick={refresh}
          disabled={refreshing}
        >
          <RefreshCw size={12} className={refreshing ? "spin" : ""} />
          {refreshing ? "Refreshing…" : "Try again"}
        </button>
      </div>
    );
  }

  if (!overview) return null;

  const allNeeds = overview.needs || [];
  const needsToShow = showAllNeeds ? allNeeds : allNeeds.slice(0, 3);
  const hasMoreNeeds = allNeeds.length > 3;

  return (
    <div className="overview-pane">
      <header className="overview-header">
        <h1 className="overview-greeting">{overview.greeting || "Hi 🐾"}</h1>
        <button
          className="overview-refresh"
          onClick={refresh}
          disabled={refreshing}
          title="Refresh the overview"
          aria-label="Refresh"
        >
          <RefreshCw size={14} className={refreshing ? "spin" : ""} />
        </button>
      </header>

      {overview.summary && (
        <p className="overview-summary">{overview.summary}</p>
      )}

      {overview.focus?.length > 0 && (
        <section className="overview-section">
          <h2 className="overview-section-title">Today's focus</h2>
          <div className="overview-card-list">
            {overview.focus.map((f) => {
              const task = taskById[f.task_id];
              if (!task) return null;
              return (
                <div key={f.task_id} className="overview-card overview-focus-card">
                  <div className="overview-card-body">
                    <div className="overview-card-title">{task.title}</div>
                    {f.why && <div className="overview-card-why">{f.why}</div>}
                  </div>
                  <button
                    className="btn btn-sm"
                    onClick={() => navigate("/tasks")}
                  >
                    Open task
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {allNeeds.length > 0 && (
        <section className="overview-section">
          <h2 className="overview-section-title">A few things need you</h2>
          <div className="overview-card-list">
            {needsToShow.map((n) => {
              const pup = pupdateById[n.pupdate_id];
              if (!pup) return null;
              const action = resolveAction(pup);
              const go = action ? (e) => {
                e.stopPropagation();
                if (action.artifact) { setArtifactModal(pup); return; }
                if (action.href) window.open(action.href, "_blank", "noreferrer");
                else navigate(action.to);
              } : null;
              const originalAsk = pup.metadata?.original_ask;
              const originalNonGoals = pup.metadata?.original_non_goals;
              const askedAt = pup.metadata?.asked_at;
              return (
                <div key={n.pupdate_id} className="overview-card">
                  <div
                    className="overview-card-body"
                    onClick={go || undefined}
                    style={{ cursor: go ? "pointer" : "default" }}
                  >
                    <div className="overview-card-title">{n.summary}</div>
                    {originalAsk && (
                      <div className="overview-card-refresher">
                        you asked{askedAt ? ` ${relativeTime(askedAt)}` : ""}: "{originalAsk.length > 120 ? originalAsk.slice(0, 117) + "…" : originalAsk}"
                      </div>
                    )}
                    {originalNonGoals && (
                      <div className="overview-card-refresher overview-card-refresher-ng">
                        must not: {originalNonGoals.length > 120 ? originalNonGoals.slice(0, 117) + "…" : originalNonGoals}
                      </div>
                    )}
                    {pup.timestamp && (
                      <div className="overview-card-meta">
                        {relativeTime(pup.timestamp)}
                      </div>
                    )}
                  </div>
                  {action && (
                    <button className="btn btn-sm btn-primary" onClick={go}>
                      {action.label}
                    </button>
                  )}
                  <button
                    className="btn-ghost overview-dismiss"
                    onClick={() => handleDismiss(pup.id)}
                    title="Dismiss"
                    aria-label="Dismiss"
                  >
                    <X size={12} />
                  </button>
                </div>
              );
            })}
            {hasMoreNeeds && (
              <button
                className="overview-more-toggle"
                onClick={() => setShowAllNeeds((v) => !v)}
              >
                {showAllNeeds
                  ? "Show less"
                  : `+ ${allNeeds.length - 3} more`}
              </button>
            )}
          </div>
        </section>
      )}

      {overview.alive && (
        <p className="overview-alive">{overview.alive}</p>
      )}

      {overview.closing && (
        <section className="overview-closing">
          <div className="overview-closing-label">Enough for today</div>
          <p className="overview-closing-body">{overview.closing}</p>
          {overview.overnight?.length > 0 && (
            <div className="overview-overnight">
              <div className="overview-overnight-label">Continuing overnight</div>
              <ul className="overview-overnight-list">
                {overview.overnight.map((t) => (
                  <li key={t.task_id} className="overview-overnight-item">
                    <span className="overview-overnight-title">{t.title}</span>
                    {t.agent_name && (
                      <span className="overview-overnight-agent">— {t.agent_name}</span>
                    )}
                  </li>
                ))}
              </ul>
              <a
                className="overview-overnight-link"
                onClick={(e) => { e.preventDefault(); navigate("/tasks"); }}
                href="/tasks"
              >
                open Tasks to amend
              </a>
            </div>
          )}
        </section>
      )}

      {overview.custom_section && (
        <section className="overview-custom">
          <div
            className="brief-content"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(overview.custom_section) }}
          />
        </section>
      )}

      {generatedAt && (
        <footer className="overview-footer">
          Generated {relativeTime(generatedAt)}
        </footer>
      )}

      {artifactModal && (
        <div className="modal-overlay" onClick={() => setArtifactModal(null)}>
          <div className="brief-modal" onClick={(e) => e.stopPropagation()}>
            <div className="brief-modal-header">
              <FileText size={18} />
              <span>{artifactModal.title || "Result"}</span>
              <button
                className="btn btn-sm"
                onClick={() => setArtifactModal(null)}
                style={{ marginLeft: "auto" }}
              >
                Close
              </button>
            </div>
            <div className="brief-modal-body">
              {artifactModal.body ? (
                <div
                  className="brief-content"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(artifactModal.body) }}
                />
              ) : (
                <div className="focus-empty">Nothing attached yet.</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
