import { useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink } from "@icons";
import { renderMarkdown } from "../../utils/markdown";



/** Compact "triggered by" card — shows the pupdate that fired the
 *  automation so the user has context without clicking through.
 *  Collapsed by default; clicks expand to show the body. */
export default function PupdateSnapshot({ snap }) {
  const [open, setOpen] = useState(false);
  if (!snap) return null;
  const hasBody = !!(snap.body && snap.body.trim());
  return (
    <div className="pupdate-snapshot">
      <button
        type="button"
        className="pupdate-snapshot-head"
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        title={hasBody ? "Show triggering pupdate" : "Triggering pupdate"}
      >
        {hasBody && (open ? <ChevronDown size={10} /> : <ChevronRight size={10} />)}
        <span className="pupdate-snapshot-label">Triggered by</span>
        {snap.source && <span className="pupdate-snapshot-tag">{snap.source}</span>}
        {snap.type && <span className="pupdate-snapshot-tag">{snap.type}</span>}
        <span className="pupdate-snapshot-title">{snap.title}</span>
        {snap.url && (
          <a
            href={snap.url}
            target="_blank"
            rel="noreferrer"
            className="pupdate-snapshot-link"
            onClick={(e) => e.stopPropagation()}
            title="Open source"
          >
            <ExternalLink size={9} />
          </a>
        )}
      </button>
      {open && hasBody && (
        <div
          className="pupdate-snapshot-body markdown"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(snap.body) }}
        />
      )}
    </div>
  );
}
