import { useEffect, useState, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { renderMarkdown } from "../utils/markdown";
import { relativeTime } from "../utils/dates";
import {
  Sunrise, RefreshCw, FileText, X, Loader, Brain,
  MoreHorizontal, ListTodo, Search, ExternalLink,
} from "lucide-react";
import TaskCard from "./TaskCard";
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
  const [pendingLearnings, setPendingLearnings] = useState([]);
  const [showAllNeeds, setShowAllNeeds] = useState(false);
  const [artifactModal, setArtifactModal] = useState(null);
  // Which needs-card's action menu is open (pupdate_id), or null. Only
  // one menu is ever open at a time — a single shared ref is enough.
  const [menuOpenFor, setMenuOpenFor] = useState(null);
  const menuRef = useRef(null);
  // Focus section uses the full TaskCard. Track which one is expanded
  // and enough lookup data (projects, agentNames) for the card's inline
  // chrome to render. Fetched alongside the overview in fetchAll.
  const [focusExpanded, setFocusExpanded] = useState(null);
  const [projects, setProjects] = useState([]);
  const [agentNames, setAgentNames] = useState({});
  // Easter egg: 1-in-50 chance Maiko delivers the overview in Papyrus.
  // Rolled fresh on every fetch + manual refresh, so it's rare, not
  // sticky, and refreshing lets you escape (98% chance).
  const [papyrusMode, setPapyrusMode] = useState(false);
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
    setPapyrusMode(Math.random() < 0.02);
    try {
      const [overviewRes, pupRes, taskRes, pendingRes, projectRes, profileRes] = await Promise.all([
        api.getHomeOverview(),
        api.getPupdates(),
        api.getTasks(),
        api.getLearnings({ status: "pending" }).catch(() => []),
        api.getProjects().catch(() => []),
        api.getProfiles().catch(() => []),
      ]);
      setOverview(overviewRes.overview);
      setGeneratedAt(overviewRes.generated_at);
      setPupdates(pupRes);
      setTasks(taskRes);
      setProjects(projectRes || []);
      setAgentNames(Object.fromEntries((profileRes || []).map((p) => [p.id, p.display_name])));
      setPendingLearnings(pendingRes || []);
    } catch (err) {
      setError(err.message || "Overview unavailable");
    }
    setLoading(false);
  };

  const refresh = async () => {
    setRefreshing(true);
    setPapyrusMode(Math.random() < 0.02);
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

  /** Quick-action: turn the pupdate into a Task of the given type,
   *  link it back via source_pupdate_id for provenance, drop the
   *  pupdate from the needs list. `type` defaults to "todo" — pass
   *  "investigation" to route the task to an investigator agent on
   *  the next cycle tick. */
  const handleMakeTask = async (pup, type = "todo") => {
    try {
      const repo = pup.metadata?.repo || pup.metadata?.repository || pup.extra?.repo;
      const toastMsg = type === "investigation"
        ? "Investigation queued 🐾"
        : "Task created 🐾";
      await api.createTask({
        title: pup.title || "New task",
        type,
        priority: pup.priority || "normal",
        url: pup.url || "",
        source_pupdate_id: pup.id,
        tags: ["from_pupdate"],
        metadata: {
          description: pup.body || "",
          repo: repo || "",
          from_pupdate: pup.id,
        },
      });
      showToast(toastMsg, "normal");
      await api.dismissPupdate(pup.id).catch(() => {});
      setPupdates((prev) => prev.filter((p) => p.id !== pup.id));
      if (overview?.needs) {
        setOverview({
          ...overview,
          needs: overview.needs.filter((n) => n.pupdate_id !== pup.id),
        });
      }
    } catch (err) {
      showToast(err.message || "Couldn't create task", "high");
    }
  };

  const handleOpenSource = (pup) => {
    if (pup.url) window.open(pup.url, "_blank", "noreferrer");
  };

  // TaskCard's action handler — status transitions + launch. Modal-
  // requiring actions (assign, edit, detail) drop the user on the
  // /tasks page with the card expanded, where the full machinery
  // lives. Keeps the focus row on Home scannable without cloning
  // every modal over here.
  const handleTaskAction = async (e, id, action) => {
    e.stopPropagation();
    try {
      if (action === "start") await api.startTask(id);
      else if (action === "done") await api.completeTask(id);
      else if (action === "cancel") await api.cancelTask(id);
      else if (action === "launch") {
        await api.launchTask(id);
        showToast("On the way 🐾", "normal");
      }
      fetchAll();
    } catch (err) {
      showToast("Couldn't " + action + ": " + (err.message || "unknown"), "high");
    }
  };
  const goToTasks = () => navigate("/tasks");

  // Close the action menu on outside click or Escape. The click
  // handler runs AFTER React's onClick (which sets the state), so
  // toggling from the menu button works without racing: when menu
  // opens, menuRef is still null on this tick, so the close check
  // short-circuits.
  useEffect(() => {
    if (!menuOpenFor) return;
    const onDocClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setMenuOpenFor(null);
      }
    };
    const onKey = (e) => { if (e.key === "Escape") setMenuOpenFor(null); };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpenFor]);

  useEffect(() => { fetchAll(); }, []);

  // Pending-learnings count goes stale when the user approves/dismisses
  // elsewhere (Brain page, inline on a learning). The overview itself
  // is heavy to refetch (LLM-cached), so poll just the pending list on
  // an interval and also whenever the window regains focus — catches
  // the common "approve on another tab, come back" case without a full
  // overview regen.
  useEffect(() => {
    const refresh = async () => {
      try {
        const list = await api.getLearnings({ status: "pending" });
        setPendingLearnings(list || []);
      } catch {
        /* non-fatal */
      }
    };
    const id = setInterval(refresh, 30_000);
    window.addEventListener("focus", refresh);
    return () => {
      clearInterval(id);
      window.removeEventListener("focus", refresh);
    };
  }, []);

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
    <div className={`overview-pane ${papyrusMode ? "papyrus-mode" : ""}`}>
      <header className="overview-header">
        <div className="overview-greeting-wrap">
          {/* Sprite mood picked by the overview LLM from the files
              in public/sprites/ (backend scans + validates so we
              never render a bogus name). Falls back to
              maiko-greeting.png if the LLM didn't pick one. The
              onError handler hides the img silently if neither
              file exists yet, so the UI works before any sprites
              are saved. */}
          <img
            className="overview-greeting-sprite"
            src={
              overview.sprite
                ? `/sprites/maiko-${overview.sprite}.png`
                : "/sprites/maiko-greeting.png"
            }
            alt=""
            aria-hidden="true"
            onError={(e) => { e.currentTarget.style.display = "none"; }}
          />
          <h1 className="overview-greeting">{overview.greeting || "Hi 🐾"}</h1>
        </div>
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

      {overview.summary && (() => {
        // Split into a bold-ish "lead" sentence and the rest so the
        // Home overview reads greeting → punchy one-liner → body
        // instead of one dense paragraph. Splits on the first ". ",
        // "! ", or "? " so exclamations / questions survive. If the
        // summary is a single sentence, `rest` is empty and we just
        // render the lead.
        const match = overview.summary.match(/^(.+?[.!?])(\s+)(.+)$/s);
        const lead = match ? match[1] : overview.summary;
        const rest = match ? match[3] : "";
        return (
          <>
            <p className="overview-lead">{lead}</p>
            {rest && <p className="overview-summary">{rest}</p>}
          </>
        );
      })()}

      {overview.focus?.length > 0 && (
        <section className="overview-section">
          <h2 className="overview-section-title">Current focus</h2>
          <div className="overview-focus-list">
            {overview.focus.map((f) => {
              const task = taskById[f.task_id];
              if (!task) return null;
              return (
                <div key={f.task_id} className="overview-focus-entry">
                  {f.why && (
                    <div className="overview-focus-why">{f.why}</div>
                  )}
                  <TaskCard
                    task={task}
                    isExpanded={focusExpanded === task.id}
                    onToggleExpand={() => setFocusExpanded(
                      focusExpanded === task.id ? null : task.id,
                    )}
                    onAction={handleTaskAction}
                    onAssignAgent={goToTasks}
                    onEdit={goToTasks}
                    onShowDetail={goToTasks}
                    onRefresh={fetchAll}
                    projects={projects}
                    agentNames={agentNames}
                  />
                </div>
              );
            })}
          </div>
        </section>
      )}

      {allNeeds.length > 0 && (
        <section className="overview-section">
          <h2 className="overview-section-title">What I'd start with</h2>
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
              // Cards with no primary CTA fall back to "click anywhere
              // on the body opens the action menu" — instead of just
              // sitting there inert, which made it unclear the card
              // was even interactive.
              const onBodyClick = go || ((e) => {
                e.stopPropagation();
                setMenuOpenFor(pup.id);
              });
              const originalAsk = pup.metadata?.original_ask;
              const originalNonGoals = pup.metadata?.original_non_goals;
              const askedAt = pup.metadata?.asked_at;
              const menuOpen = menuOpenFor === pup.id;
              return (
                <div key={n.pupdate_id} className="overview-card">
                  <div
                    className="overview-card-body"
                    onClick={onBodyClick}
                    style={{ cursor: "pointer" }}
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
                  <div className="overview-card-menu-wrap">
                    <button
                      className="btn-ghost overview-card-menu-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenuOpenFor(menuOpen ? null : pup.id);
                      }}
                      title="More actions"
                      aria-label="More actions"
                      aria-expanded={menuOpen}
                    >
                      <MoreHorizontal size={14} />
                    </button>
                    {menuOpen && (
                      <div className="overview-card-menu" ref={menuRef}>
                        <button
                          className="overview-card-menu-item"
                          onClick={(e) => {
                            e.stopPropagation();
                            setMenuOpenFor(null);
                            handleMakeTask(pup, "todo");
                          }}
                        >
                          <ListTodo size={13} /> Make a todo
                        </button>
                        <button
                          className="overview-card-menu-item"
                          onClick={(e) => {
                            e.stopPropagation();
                            setMenuOpenFor(null);
                            handleMakeTask(pup, "investigation");
                          }}
                        >
                          <Search size={13} /> Investigate with an agent
                        </button>
                        {pup.url && (
                          <button
                            className="overview-card-menu-item"
                            onClick={(e) => {
                              e.stopPropagation();
                              setMenuOpenFor(null);
                              handleOpenSource(pup);
                            }}
                          >
                            <ExternalLink size={13} /> Open source
                          </button>
                        )}
                        <button
                          className="overview-card-menu-item overview-card-menu-item-muted"
                          onClick={(e) => {
                            e.stopPropagation();
                            setMenuOpenFor(null);
                            handleDismiss(pup.id);
                          }}
                        >
                          <X size={13} /> Dismiss
                        </button>
                      </div>
                    )}
                  </div>
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

      {pendingLearnings.length > 0 && (
        <section className="overview-section">
          <h2 className="overview-section-title">
            New learnings to review
          </h2>
          <div className="overview-learnings-card" onClick={() => navigate("/knowledge?tab=pending")}>
            <div className="overview-learnings-icon"><Brain size={16} /></div>
            <div className="overview-learnings-body">
              <div className="overview-learnings-count">
                {pendingLearnings.length} learning{pendingLearnings.length === 1 ? "" : "s"} waiting for your nod
              </div>
              <ul className="overview-learnings-preview">
                {pendingLearnings.slice(0, 3).map((l) => (
                  <li key={l.id}>{l.rule}</li>
                ))}
                {pendingLearnings.length > 3 && (
                  <li className="overview-learnings-more">
                    + {pendingLearnings.length - 3} more
                  </li>
                )}
              </ul>
            </div>
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
