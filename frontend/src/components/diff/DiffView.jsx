import { useMemo } from "react";
import { Diff, Hunk, parseDiff, getChangeKey } from "react-diff-view";
import "react-diff-view/style/index.css";

/**
 * Thin wrapper over react-diff-view. Parses a raw unified diff, renders
 * each file as a collapsible section, and threads per-line widgets
 * (comment pins, draft forms, comment threads) into the gutter via
 * the library's `widgets` prop.
 *
 * Props:
 *   rawDiff       — string, unified diff output from `git diff`
 *   widgetsByKey  — { [changeKey]: ReactNode } rendered after that line
 *   onLineClick   — (file_path, line_number, side, changeKey) => void,
 *                   fired when the user clicks a diff row to leave a
 *                   new comment
 *   viewType      — "split" | "unified" (default "unified" — nicer for
 *                   inline comments)
 */
export default function DiffView({ rawDiff, widgetsByKey = {}, onLineClick, viewType = "unified" }) {
  const files = useMemo(() => {
    if (!rawDiff) return [];
    try {
      return parseDiff(rawDiff);
    } catch (err) {
      console.error("DiffView: failed to parse diff", err);
      return [];
    }
  }, [rawDiff]);

  if (!rawDiff) return <div className="diff-view-empty">No diff yet — agent is still working.</div>;
  if (files.length === 0) return <div className="diff-view-empty">Empty diff (no changes).</div>;

  return (
    <div className="diff-view">
      {files.map((file, i) => (
        <FileBlock
          key={`${file.oldPath}-${file.newPath}-${i}`}
          file={file}
          viewType={viewType}
          widgetsByKey={widgetsByKey}
          onLineClick={onLineClick}
        />
      ))}
    </div>
  );
}

function FileBlock({ file, viewType, widgetsByKey, onLineClick }) {
  const displayPath = file.newPath !== "/dev/null" ? file.newPath : file.oldPath;

  const widgets = useMemo(() => {
    // Filter widgets to just the changes in this file's hunks so
    // react-diff-view doesn't warn about orphans.
    const changeKeys = new Set();
    for (const hunk of file.hunks || []) {
      for (const change of hunk.changes || []) {
        changeKeys.add(getChangeKey(change));
      }
    }
    const filtered = {};
    for (const [key, node] of Object.entries(widgetsByKey || {})) {
      if (changeKeys.has(key)) filtered[key] = node;
    }
    return filtered;
  }, [file, widgetsByKey]);

  const handleGutterClick = (change) => {
    if (!onLineClick) return;
    const side = change.type === "delete" ? "old" : "new";
    const line = change.type === "delete" ? change.lineNumber : change.newLineNumber || change.lineNumber;
    onLineClick(displayPath, line, side, getChangeKey(change));
  };

  return (
    <div className="diff-file-block">
      <div className="diff-file-header">
        <span className="diff-file-path">{displayPath}</span>
        {file.type && <span className="diff-file-type">{file.type}</span>}
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
