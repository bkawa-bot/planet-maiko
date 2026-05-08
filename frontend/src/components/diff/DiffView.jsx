import { useEffect, useMemo, useRef, useState } from "react";
import { Diff, Hunk, parseDiff, getChangeKey, tokenize } from "react-diff-view";
import "react-diff-view/style/index.css";
// refractor v5 ships an exports map that only exposes `refractor`
// (common bundle), `refractor/core`, `refractor/all`, and individual
// languages as `refractor/<lang>`. Explicit `refractor/lang/*.js`
// paths are blocked by the exports field — Vite/Rollup honors that
// and the build fails to resolve. Use the package-name + language
// shape instead.
import { refractor } from "refractor/core";
import javascript from "refractor/javascript";
import jsx from "refractor/jsx";
import typescript from "refractor/typescript";
import tsx from "refractor/tsx";
import python from "refractor/python";
import json from "refractor/json";
import css from "refractor/css";
import scss from "refractor/scss";
import bash from "refractor/bash";
import markdown from "refractor/markdown";
import yaml from "refractor/yaml";
import "./diff-syntax.css";

// Register the languages we actually review. Anything not in this set
// falls back to plaintext rendering — no highlighting but no crash.
// Keep the list tight: each language adds ~3-8KB to the bundle. Add
// more as the team's review surface widens.
[
  javascript, jsx, typescript, tsx, python,
  json, css, scss, bash, markdown, yaml,
].forEach((lang) => {
  try { refractor.register(lang); } catch { /* already registered or refractor variant differs */ }
});

// File-extension → refractor language alias. Falls through to null
// when the extension isn't one we registered, which the tokenize
// pass uses as the "skip highlight" signal.
const EXT_TO_LANG = {
  js: "javascript", mjs: "javascript", cjs: "javascript",
  jsx: "jsx",
  ts: "typescript",
  tsx: "tsx",
  py: "python", pyi: "python",
  json: "json",
  css: "css",
  scss: "scss",
  sh: "bash", bash: "bash", zsh: "bash",
  md: "markdown", markdown: "markdown",
  yml: "yaml", yaml: "yaml",
};

function languageFromPath(path) {
  if (!path) return null;
  const m = path.match(/\.([^./\\]+)$/);
  if (!m) return null;
  return EXT_TO_LANG[m[1].toLowerCase()] || null;
}

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

  // Tokenize each file's hunks against its detected language. Memoized
  // on the file identity so re-renders (anchor updates, focus changes)
  // don't re-tokenize. Wrapped in try/catch so a bad language guess on
  // exotic content can't crash the whole page — we just fall through
  // to plain rendering. ~3-8KB extra bundle per language registered
  // up top; tokenize itself is O(diff lines) so even big files render
  // in tens of ms.
  const tokens = useMemo(() => {
    const language = languageFromPath(displayPath);
    if (!language) return undefined;
    try {
      return tokenize(file.hunks, {
        highlight: true,
        refractor,
        language,
      });
    } catch (err) {
      console.warn(`[diff] tokenize failed for ${displayPath}:`, err);
      return undefined;
    }
  }, [file.hunks, displayPath]);

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
        tokens={tokens}
        widgets={widgets}
        gutterEvents={{ onClick: ({ change }) => handleGutterClick(change) }}
      >
        {(hunks) => hunks.map((h) => <Hunk key={h.content} hunk={h} />)}
      </Diff>
    </div>
  );
}
