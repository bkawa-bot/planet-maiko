import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight, X } from "@icons";
import CardAvatar from "../CardAvatar";
import { renderMarkdown } from "../../utils/markdown";
import { formatRepo } from "../../utils/repo";
import { relativeTime } from "../../utils/dates";



/** Inline artifact row for done AgentJob reports (cartograph walks,
 *  investigation findings, standalone skill runs). Click the row to
 *  expand the artifact body in place — no navigation, since there's
 *  no dedicated viewer page yet. Dismiss flips extra.reviewed=true
 *  on the job so the row falls out of the home_api filter. */
export default function ArtifactRow({ it, meta, Icon, profile, onDismiss, defaultOrg }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const hasBody = !!(it.body && it.body.trim());
  // When the row has a route, the user has two ways in: click the
  // header to navigate to /jobs/<id> (full page with markdown + chat
  // for follow-ups), or click the chevron to expand inline for a
  // quick peek without leaving Home. When there's no route, the
  // header click expands inline as the primary interaction.
  const hasRoute = !!it.route;
  const handleHeaderClick = () => {
    if (hasRoute) navigate(it.route);
    else if (hasBody) setOpen((v) => !v);
  };
  const togglePeek = (e) => {
    e.stopPropagation();
    setOpen((v) => !v);
  };
  return (
    <div className={`review-queue-row tone-${meta.tone} review-queue-row-artifact`}>
      <button
        type="button"
        className="review-queue-row-main"
        onClick={handleHeaderClick}
        disabled={!hasBody && !hasRoute}
        aria-expanded={open}
      >
        {profile ? (
          <div className="review-queue-icon review-queue-icon-avatar">
            <CardAvatar agent={profile} size={32} />
          </div>
        ) : (
          <div className="review-queue-icon">
            <Icon size={14} />
          </div>
        )}
        <div className="review-queue-body">
          <div className="review-queue-title">{it.title || "(untitled)"}</div>
          <div className="review-queue-meta">
            <span className="review-queue-kind">{meta.label}</span>
            {it.kind_label && it.kind_label !== it.kind && (
              <span className="review-queue-subkind">{it.kind_label}</span>
            )}
            {it.repo && (
              <span className="review-queue-repo" title={it.repo}>
                {formatRepo(it.repo, defaultOrg)}
              </span>
            )}
            {it.agent_name && (
              <span className="review-queue-agent">by {it.agent_name}</span>
            )}
            {it.timestamp && (
              <span className="review-queue-time">
                {relativeTime(it.timestamp)}
              </span>
            )}
          </div>
        </div>
        {hasRoute && <span className="review-queue-cta">Open report →</span>}
      </button>
      {hasBody && (
        <button
          type="button"
          className="btn btn-sm btn-ghost memos-pane-peek"
          onClick={togglePeek}
          title={open ? "Hide preview" : "Quick peek without leaving Home"}
          aria-expanded={open}
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
      )}
      {open && hasBody && (
        <div className="review-queue-artifact-body">
          <div
            className="markdown"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(it.body) }}
          />
          {it.body_truncated && (
            <div className="review-queue-artifact-truncated">
              Report truncated.{hasRoute && " Click \"Open report\" for the full output and chat."}
            </div>
          )}
        </div>
      )}
      <button
        className="btn btn-sm btn-ghost memos-pane-dismiss"
        onClick={onDismiss}
        title="Mark as seen"
      >
        <X size={12} />
      </button>
    </div>
  );
}