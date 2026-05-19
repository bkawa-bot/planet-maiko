import { useEffect, useState, useMemo, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { renderMarkdown } from "../utils/markdown";
import { relativeTime } from "../utils/dates";
import {
  Sunrise, RefreshCw, FileText, X,
  MoreHorizontal, ListTodo, Search, ExternalLink, LogicGateNot,
} from "@icons";
import TaskCard from "./TaskCard";
import ModalPortal from "./ModalPortal";
import PlanetSpinner from "./PlanetSpinner";
import LearningsCard from "./overview/LearningsCard";
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

// Memo → action resolver. Given the memo referenced in overview.needs,
// returns where the primary button should go. Returns null when
// there's no useful destination — caller hides the button so the user
// doesn't click an "Open" that bounces back to Home.
function resolveAction(m) {
  if (!m) return null;
  const taskId = m.source_task_id || m.extra?.task_id;
  const jobId = m.extra?.job_id;
  const kind = m.kind;

  if (kind === "agent_plan" && jobId) {
    return { label: m.cta_label || "Review plan", to: m.url || `/jobs/${jobId}?view=plan` };
  }
  if (kind === "agent_ready" && jobId) {
    return { label: m.cta_label || "Review diff", to: m.url || `/jobs/${jobId}?view=diff` };
  }
  if (kind === "agent_stuck" && jobId) {
    return { label: m.cta_label || "Help out", to: m.url || `/jobs/${jobId}?view=chat` };
  }
  if (kind === "job_approval") {
    // Inline approve on the Memos pane; no nav destination.
    return null;
  }
  if (kind === "notification") {
    return m.url ? { label: "Open", href: m.url } : null;
  }
  if (kind === "skill_result") {
    // Output lives on the Recent Skills widget; no nav needed from here.
    return null;
  }
  if (kind === "agent_proposal") {
    // The Memos pane renders ProposalCard inline for approve/edit/dismiss;
    // the overview just calls it out narratively.
    return null;
  }
  // Unknown or unhandled kind — render a read-only card.
  return null;
}

export default function OverviewPane() {
  const [overview, setOverview] = useState(null);
  const [generatedAt, setGeneratedAt] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [memos, setMemos] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [pendingLearnings, setPendingLearnings] = useState([]);
  // Canonical count from /brain/status. The pendingLearnings list above
  // is capped by the /learnings API's default limit (200) — using its
  // length as a "how many" signal is wrong when the actual DB has more.
  // The brain widget on Home uses the same source so the two surfaces
  // can't disagree.
  const [pendingLearningsCount, setPendingLearningsCount] = useState(0);
  const [showAllNeeds, setShowAllNeeds] = useState(false);
  const [artifactModal, setArtifactModal] = useState(null);
  // Which needs-card's action menu is open (memo_id), or null. Only
  // one menu is ever open at a time — a single shared ref is enough.
  const [menuOpenFor, setMenuOpenFor] = useState(null);
  const menuRef = useRef(null);
  // Focus section uses the full TaskCard. Track which one is expanded
  // and enough lookup data (projects, agentNames) for the card's inline
  // chrome to render. Fetched alongside the overview in fetchAll.
  const [focusExpanded, setFocusExpanded] = useState(null);
  const [projects, setProjects] = useState([]);
  const [agentNames, setAgentNames] = useState({});
  const navigate = useNavigate();

  const memoById = useMemo(() => {
    const idx = {};
    for (const m of memos) idx[m.id] = m;
    return idx;
  }, [memos]);

  const taskById = useMemo(() => {
    const m = {};
    for (const t of tasks) m[t.id] = t;
    return m;
  }, [tasks]);

  // Stale notifications: unactioned (pending OR seen) notification
  // memos older than 24h. Computed up here with the other useMemo
  // calls so it runs on every render — the early returns for
  // loading / error / no-overview below would skip a useMemo
  // declared after them and trip "rendered more hooks than during
  // the previous render".
  const STALE_HOURS = 24;
  const staleNotificationCount = useMemo(() => {
    const now = Date.now();
    return memos.filter((m) => {
      if (m.kind !== "notification") return false;
      if (m.status && m.status !== "pending" && m.status !== "seen") return false;
      const created = m.created_at ? new Date(m.created_at).getTime() : null;
      if (!created) return false;
      return (now - created) / 1000 / 3600 >= STALE_HOURS;
    }).length;
  }, [memos]);

  // Tracks whether the overview we're currently rendering is stale —
  // backend now serves stale-or-placeholder immediately and regens on
  // a daemon thread, so the frontend should poll until it lands fresh.
  const [overviewStale, setOverviewStale] = useState(false);

  const fetchAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [overviewRes, memoRes, taskRes, pendingRes, projectRes, profileRes, brainStatusRes] = await Promise.all([
        api.getHomeOverview(),
        api.getMemos({ limit: 200 }),
        api.getTasks(),
        api.getLearnings({ status: "pending" }).catch(() => []),
        api.getProjects().catch(() => []),
        api.getProfiles().catch(() => []),
        api.getBrainStatus().catch(() => null),
      ]);
      setOverview(overviewRes.overview);
      setGeneratedAt(overviewRes.generated_at);
      setOverviewStale(!!overviewRes.stale_triggered_regen);
      setMemos(memoRes || []);
      setTasks(taskRes);
      setProjects(projectRes || []);
      setAgentNames(Object.fromEntries((profileRes || []).map((p) => [p.id, p.display_name])));
      setPendingLearnings(pendingRes || []);
      setPendingLearningsCount(
        brainStatusRes?.pending?.pending_learnings ?? (pendingRes?.length || 0)
      );
    } catch (err) {
      setError(err.message || "Overview unavailable");
    }
    setLoading(false);
  };

  // Background regen poll: when the cached overview comes back stale,
  // the backend kicks a regen thread. Poll the GET endpoint on a soft
  // cadence until it responds with stale=false (regen landed), then
  // stop. Frontend doesn't need to wait synchronously for the LLM.
  useEffect(() => {
    if (!overviewStale) return;
    let cancelled = false;
    const POLL_MS = 30_000;
    const tick = async () => {
      if (cancelled) return;
      try {
        const res = await api.getHomeOverview();
        if (cancelled) return;
        if (res?.overview) {
          setOverview(res.overview);
          setGeneratedAt(res.generated_at);
        }
        if (!res?.stale_triggered_regen) {
          setOverviewStale(false);
          return;
        }
      } catch { /* try again next tick */ }
      if (!cancelled) setTimeout(tick, POLL_MS);
    };
    const timer = setTimeout(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [overviewStale]);

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

  const handleDismiss = async (memoId) => {
    try {
      await api.dismissMemo(memoId);
      setMemos((prev) => prev.filter((m) => m.id !== memoId));
      // Optimistic: drop from overview.needs locally so the row
      // disappears without waiting for a regeneration.
      if (overview?.needs) {
        setOverview({
          ...overview,
          needs: overview.needs.filter((n) => n.memo_id !== memoId),
        });
      }
    } catch (err) {
      showToast("Couldn't dismiss", "high");
    }
  };

  /** Quick-action: turn the memo into a Task of the given type.
   *  `type` defaults to "todo" — pass "investigation" to route the
   *  task to an investigator agent on the next cycle tick. Dismisses
   *  the memo once the task is created.
   */
  const handleMakeTask = async (memo, type = "todo") => {
    try {
      const repo = memo.extra?.repo || memo.extra?.draft?.repo;
      const toastMsg = type === "investigation"
        ? "Investigation queued 🐾"
        : "Task created 🐾";
      const res = await api.createTask({
        title: memo.title || "New task",
        type,
        priority: memo.priority || "normal",
        url: memo.url || "",
        tags: ["from_memo"],
        // Investigation should actually run, not just land unassigned
        // on the Tasks page. auto_launch routes an agent + queues the
        // job inline (backend no-ops it for plain todos).
        auto_launch: type === "investigation",
        metadata: {
          description: memo.body || "",
          repo: repo || "",
          from_memo_id: memo.id,
        },
      });
      const launchedMsg = type === "investigation" && res?.auto_launched === false
        ? "Investigation task created (no investigator agent available to auto-run it)"
        : toastMsg;
      showToast(launchedMsg, "normal");
      await api.dismissMemo(memo.id).catch(() => {});
      setMemos((prev) => prev.filter((m) => m.id !== memo.id));
      if (overview?.needs) {
        setOverview({
          ...overview,
          needs: overview.needs.filter((n) => n.memo_id !== memo.id),
        });
      }
    } catch (err) {
      showToast(err.message || "Couldn't create task", "high");
    }
  };

  const handleOpenSource = (memo) => {
    if (memo.url) window.open(memo.url, "_blank", "noreferrer");
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
        const [list, brainStatus] = await Promise.all([
          api.getLearnings({ status: "pending" }),
          api.getBrainStatus().catch(() => null),
        ]);
        setPendingLearnings(list || []);
        setPendingLearningsCount(
          brainStatus?.pending?.pending_learnings ?? (list?.length || 0)
        );
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
        <PlanetSpinner size={18} />
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

      {staleNotificationCount > 0 && (
        <p className="overview-stale-line">
          {staleNotificationCount} stale notification{staleNotificationCount === 1 ? "" : "s"} — sitting in your inbox for a day or more.
        </p>
      )}

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
              const memo = memoById[n.memo_id];
              if (!memo) return null;
              const action = resolveAction(memo);
              const go = action ? (e) => {
                e.stopPropagation();
                if (action.artifact) { setArtifactModal(memo); return; }
                if (action.href) window.open(action.href, "_blank", "noreferrer");
                else navigate(action.to);
              } : null;
              // Cards with no primary CTA fall back to "click anywhere
              // on the body opens the action menu" — instead of just
              // sitting there inert, which made it unclear the card
              // was even interactive.
              const onBodyClick = go || ((e) => {
                e.stopPropagation();
                setMenuOpenFor(memo.id);
              });
              const originalAsk = memo.extra?.original_ask;
              const originalNonGoals = memo.extra?.original_non_goals;
              const askedAt = memo.extra?.asked_at;
              const menuOpen = menuOpenFor === memo.id;
              return (
                <div key={n.memo_id} className="overview-card">
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
                    {memo.created_at && (
                      <div className="overview-card-meta">
                        {relativeTime(memo.created_at)}
                      </div>
                    )}
                  </div>
                  {action && (
                    <button className="btn btn-sm btn-primary" onClick={go}>
                      <LogicGateNot size={12} /> {action.label}
                    </button>
                  )}
                  <div className="overview-card-menu-wrap">
                    <button
                      className="btn-ghost overview-card-menu-btn"
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenuOpenFor(menuOpen ? null : memo.id);
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
                            handleMakeTask(memo, "todo");
                          }}
                        >
                          <ListTodo size={13} /> Make a todo
                        </button>
                        <button
                          className="overview-card-menu-item"
                          onClick={(e) => {
                            e.stopPropagation();
                            setMenuOpenFor(null);
                            handleMakeTask(memo, "investigation");
                          }}
                        >
                          <Search size={13} /> Investigate with an agent
                        </button>
                        {memo.url && (
                          <button
                            className="overview-card-menu-item"
                            onClick={(e) => {
                              e.stopPropagation();
                              setMenuOpenFor(null);
                              handleOpenSource(memo);
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
                            handleDismiss(memo.id);
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

      <LearningsCard count={pendingLearningsCount} preview={pendingLearnings} />

      {overview.alive && (
        <p className="overview-alive">{overview.alive}</p>
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
        <ModalPortal>
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
        </ModalPortal>
      )}
    </div>
  );
}
