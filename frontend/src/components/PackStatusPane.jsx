import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ClipboardCheck, FileText, HelpCircle,
  Lightbulb, Loader, Users,
} from "lucide-react";
import { api } from "../api/client";
import { relativeTime } from "../utils/dates";
import { formatRepo, useDefaultOrg } from "../utils/repo";
import PackAskBox from "./PackAskBox";
import CardAvatar from "./CardAvatar";
import "./PackStatusPane.css";

/**
 * Home's agent-centric pack status view. Replaces the old
 * PackRequestsWidget sidebar thing.
 *
 * One row per active agent-task pair:
 *   - avatar + name + state dot + role/scope chips
 *   - what they're working on (task title)
 *   - last status message (truncated)
 *   - pending pack-request inline when present (plan / review / stuck)
 *
 * Pack-internal only. External teammate events (a human asking you to
 * review their PR) flow through the review-task automation → Memos
 * pane, not this one — no point duplicating the ask here.
 *
 * Sort order: rows with a pending request first, then stuck agents,
 * then working, then idle. Capped at 10 rows.
 */

const POLL_MS = 30_000;
const MAX_ROWS = 10;

const REQUEST_META = {
  agent_plan_for_approval: {
    icon: ClipboardCheck,
    label: "plan ready for approval",
    route: (p) => `/tasks/${p.metadata?.task_id}/plan`,
    tone: "plan",
  },
  agent_ready_for_review: {
    icon: FileText,
    label: "ready for review",
    route: (p) => `/tasks/${p.metadata?.task_id}/review`,
    tone: "review",
  },
  pr_review_complete: {
    icon: FileText,
    label: "finished the review",
    route: (p) => `/tasks/${p.metadata?.task_id}/review`,
    tone: "review",
  },
  agent_working_on_feedback: {
    icon: Loader,
    label: "addressing review feedback",
    route: (p) => p.metadata?.task_id ? `/tasks/${p.metadata.task_id}` : "/",
    tone: "working",
    spin: true,
  },
  agent_stuck: {
    icon: HelpCircle,
    label: "is stuck",
    route: (p) => `/tasks/${p.metadata?.task_id}`,
    tone: "stuck",
  },
  agent_proposal: {
    icon: Lightbulb,
    label: "has an idea",
    route: () => "/",
    tone: "idea",
  },
};

const STATE_PRIORITY = { stuck: 0, working: 1, idle: 2 };

function compareRows(a, b) {
  // Rows with a pending request rank higher
  const reqA = a.request ? 1 : 0;
  const reqB = b.request ? 1 : 0;
  if (reqA !== reqB) return reqB - reqA;
  // Then by agent state (stuck first)
  const sa = STATE_PRIORITY[a.profile?.state ?? "idle"] ?? 3;
  const sb = STATE_PRIORITY[b.profile?.state ?? "idle"] ?? 3;
  if (sa !== sb) return sa - sb;
  // Then by most recent activity
  return (new Date(b.last_seen || 0)) - (new Date(a.last_seen || 0));
}

export default function PackStatusPane() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = async () => {
    try {
      const [activity, requests, profiles] = await Promise.all([
        api.getAgentActivity(),
        api.getPackRequests(),
        api.getProfiles(),
      ]);
      const profileById = Object.fromEntries((profiles || []).map((p) => [p.id, p]));
      const requestByTask = {};
      for (const r of (requests || [])) {
        if (r.metadata?.task_id) {
          requestByTask[r.metadata.task_id] = r;
        }
      }
      const merged = (activity || []).map((a) => ({
        ...a,
        profile: profileById[a.agent_id] || null,
        request: requestByTask[a.task_id] || null,
      }));
      merged.sort(compareRows);
      setRows(merged.slice(0, MAX_ROWS));
    } catch {
      // silent — pane just stays with its last-known state
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, POLL_MS);
    return () => clearInterval(id);
  }, []);

  if (loading) return null;
  // Still render the pane when the pack is idle — the ask-box belongs
  // on Home even before there are rows to show.
  return (
    <div className="pack-status-pane frost-pane">
      <div className="pack-status-header">
        <Users size={12} /> Your pack
      </div>
      <PackAskBox />
      {rows.length === 0 && (
        <div className="pack-status-empty">
          Pack's quiet. Ask something up there and I'll pick someone.
        </div>
      )}

      <div className="pack-status-list">
        {rows.map((r) => (
          <PackRow key={r.task_id} row={r} />
        ))}
      </div>
    </div>
  );
}

function PackRow({ row }) {
  const defaultOrg = useDefaultOrg();
  const profile = row.profile;
  const request = row.request;
  const state = profile?.state || "idle";
  const meta = request ? REQUEST_META[request.type] : null;
  const primaryTo = meta
    ? meta.route(request)
    : row.task_id
    ? `/tasks/${row.task_id}`
    : "/";

  const toneClass = meta ? `tone-${meta.tone}` : "";

  return (
    <Link to={primaryTo} className={`pack-row ${request ? `has-request ${toneClass}` : ""}`}>
      <div className="pack-row-avatar">
        <CardAvatar agent={profile || row} size="sm" />
      </div>
      <div className="pack-row-body">
        <div className="pack-row-title">
          <span
            className={`pack-state-dot state-${state}`}
            title={`Agent state: ${state}`}
          />
          <span className="pack-row-name">
            {profile?.display_name || row.agent_name || "Agent"}
          </span>
          {profile?.role && <span className="pack-row-chip">{profile.role}</span>}
          {profile?.scope_repo && (
            <span className="pack-row-chip" title={profile.scope_repo}>{formatRepo(profile.scope_repo, defaultOrg)}</span>
          )}
          {request && (
            <span className="pack-row-request-label">{meta.label}</span>
          )}
        </div>
        {row.task_title && (
          <div className="pack-row-task" title={row.task_title}>
            <span className="pack-row-task-prefix">
              {request ? "on" : "working on"}:
            </span>{" "}
            {row.task_title}
          </div>
        )}
        {row.last_message && (
          <div className="pack-row-update">
            "{row.last_message}"{" "}
            <span className="pack-row-time">· {relativeTime(row.last_seen)}</span>
          </div>
        )}
      </div>
    </Link>
  );
}
