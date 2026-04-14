import { useMemo } from "react";
import { Diff, Hunk, parseDiff, getChangeKey } from "react-diff-view";
import "react-diff-view/style/index.css";

/**
 * Thin wrapper over react-diff-view. Parses a raw unified diff, renders
 * each file as a collapsible section, and threads per-line widgets
 * (comment markers, draft forms) into the gutter via the library's
 * `widgets` prop.
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

  if (!rawDiff) return <div className="diff-view-empty">No diff yet — agent is still working.</div>;
  if (files.length === 0) return <div className="diff-view-empty">Empty diff (no changes).</div>;

  return (
    <div className="diff-view">
      {files.map((file, i) => (
        <FileBlock
          key={`${file.oldPath}-${file.newPath}-${i}`}
          file={file}
          viewType={viewType}
          anchors={anchors}
          onLineClick={onLineClick}
        />
      ))}
    </div>
  );
}

function FileBlock({ file, viewType, anchors, onLineClick }) {
  const displayPath = file.newPath !== "/dev/null" ? file.newPath : file.oldPath;

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
