import { useEffect, useMemo, useRef, useState } from "react";
import { Diff, Hunk, parseDiff, getChangeKey } from "react-diff-view";
import "react-diff-view/style/index.css";

/**
 * Thin wrapper over react-diff-view. Parses a raw unified diff, renders
 * each file as a section, and threads per-line widgets (comment markers,
 * draft forms) into the gutter via the library's `widgets` prop.
 *
 * A sticky filename bar pinned to the scroll container tracks whichever
 * file is currently in view — saves scrolling to remember where you are
 * in a large diff.
 *
 * Props:
 *   rawDiff       — string, unified diff output from `git diff`
 *   anchors       — { [fileKey]: ReactNode } where fileKey is
 *                   `${file_path}::${line_number}::${side}`. Each node
 *                   renders on the matching diff line. DiffView
 *                   translates to the library's internal changeKey
 *                   by walking every change in every hunk.
 *   onLineClick   — (file_path, line_number, side, changeKey) => void,
 *                   fired when the user clicks a diff row to leave a
 *                   new comment
 *   viewType      — "split" | "unified" (default "unified" — nicer for
 *                   inline comments)
 */
export default function DiffView({ rawDiff, anchors = {}, onLineClick, viewType = "unified" }) {
  const files = useMemo(() => {
    if (!rawDiff) return [];
    try {
      return parseDiff(rawDiff);
    } catch (err) {
      console.error("DiffView: failed to parse diff", err);
      return [];
    }
  }, [rawDiff]);

  const fileStats = useMemo(() => files.map(fileStatsFor), [files]);

  const blockRefs = useRef([]);
  const [activeIdx, setActiveIdx] = useState(0);

  useEffect(() => {
    if (!files.length) return;
    // Highlight the file whose header crossed the top-of-content mark
    // most recently. rootMargin expands the trigger line well below
    // the visible top so scrolling down snaps to the next file before
    // its header leaves the screen — feels like the bar is "in sync"
    // rather than "trailing".
    const observer = new IntersectionObserver(
      (entries) => {
        // Collect currently-intersecting file indexes, pick the smallest
        // (i.e. earliest/topmost file currently crossing the trigger).
        const hits = entries
          .filter((e) => e.isIntersecting)
          .map((e) => Number(e.target.dataset.idx))
          .filter((n) => !Number.isNaN(n));
        if (hits.length) {
          setActiveIdx(Math.min(...hits));
        }
      },
      { rootMargin: "-60px 0px -75% 0px", threshold: 0 }
    );
    blockRefs.current.forEach((el) => el && observer.observe(el));
    return () => observer.disconnect();
  }, [files]);

  if (!rawDiff) return <div className="diff-view-empty">No diff yet — agent is still working.</div>;
  if (files.length === 0) return <div className="diff-view-empty">Empty diff (no changes).</div>;

  const activeMeta = fileStats[activeIdx];

  return (
    <div className="diff-view">
      {activeMeta && (
        <div className="diff-sticky-bar">
          <span className={`diff-type-badge type-${activeMeta.kind}`}>
            {activeMeta.kindLabel}
          </span>
          <span className="diff-sticky-path">{activeMeta.path}</span>
          <span className="diff-sticky-stats">
            <span className="diff-stat-add">+{activeMeta.added}</span>
            <span className="diff-stat-del">−{activeMeta.removed}</span>
          </span>
          <span className="diff-sticky-counter">
            {activeIdx + 1} of {files.length}
          </span>
        </div>
      )}
      {files.map((file, i) => (
        <FileBlock
          key={`${file.oldPath}-${file.newPath}-${i}`}
          ref={(el) => (blockRefs.current[i] = el)}
          idx={i}
          file={file}
          stats={fileStats[i]}
          viewType={viewType}
          anchors={anchors}
          onLineClick={onLineClick}
        />
      ))}
    </div>
  );
}

function fileStatsFor(file) {
  let added = 0;
  let removed = 0;
  for (const hunk of file.hunks || []) {
    for (const change of hunk.changes || []) {
      if (change.isInsert) added += 1;
      else if (change.isDelete) removed += 1;
    }
  }
  // Derive a visual "kind" from react-diff-view's type field + which
  // side the file exists on. This drives the color badge.
  let kind = "modify";
  let kindLabel = "modified";
  if (file.type === "add" || file.oldPath === "/dev/null") {
    kind = "add";
    kindLabel = "added";
  } else if (file.type === "delete" || file.newPath === "/dev/null") {
    kind = "delete";
    kindLabel = "removed";
  } else if (file.type === "rename") {
    kind = "rename";
    kindLabel = "renamed";
  }
  const path = file.newPath !== "/dev/null" ? file.newPath : file.oldPath;
  return { path, added, removed, kind, kindLabel };
}

function FileBlock({ ref, file, stats, viewType, anchors, onLineClick, idx }) {
  const displayPath = stats.path;

  const widgets = useMemo(() => {
    // Walk every change in the file and map (file_path, line, side)
    // back to the library's changeKey. Normal (unchanged) lines are
    // visible on both sides in split view and once in unified; we
    // register both side variants so a comment pinned to either side
    // finds its home.
    const byAnchor = {};
    for (const hunk of file.hunks || []) {
      for (const change of hunk.changes || []) {
        const key = getChangeKey(change);
        if (change.isInsert) {
          byAnchor[`${file.newPath}::${change.lineNumber}::new`] = key;
        } else if (change.isDelete) {
          byAnchor[`${file.oldPath}::${change.lineNumber}::old`] = key;
        } else if (change.isNormal) {
          byAnchor[`${file.newPath}::${change.newLineNumber}::new`] = key;
          byAnchor[`${file.oldPath}::${change.oldLineNumber}::old`] = key;
        }
      }
    }
    const result = {};
    for (const [anchor, node] of Object.entries(anchors || {})) {
      const key = byAnchor[anchor];
      if (key) result[key] = node;
    }
    return result;
  }, [file, anchors]);

  const handleGutterClick = (change) => {
    if (!onLineClick) return;
    const side = change.type === "delete" ? "old" : "new";
    const line = change.type === "delete" ? change.lineNumber : change.newLineNumber || change.lineNumber;
    onLineClick(displayPath, line, side, getChangeKey(change));
  };

  return (
    <div className="diff-file-block" ref={ref} data-idx={idx}>
      <div className="diff-file-header">
        <span className={`diff-type-badge type-${stats.kind}`}>
          {stats.kindLabel}
        </span>
        <span className="diff-file-path">{displayPath}</span>
        <span className="diff-file-stats">
          <span className="diff-stat-add">+{stats.added}</span>
          <span className="diff-stat-del">−{stats.removed}</span>
        </span>
      </div>
      <Diff
        viewType={viewType}
        diffType={file.type}
        hunks={file.hunks}
        widgets={widgets}
        gutterEvents={{ onClick: ({ change }) => handleGutterClick(change) }}
      >
        {(hunks) => hunks.map((h) => <Hunk key={h.content} hunk={h} />)}
      </Diff>
    </div>
  );
}
