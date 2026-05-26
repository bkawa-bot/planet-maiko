/**
 * Lightweight markdown renderer for LLM output we display via
 * dangerouslySetInnerHTML. Block-based: split on blank lines, dispatch
 * each block by kind, then run inline transforms inside.
 *
 * Supports: # ## ### headers, **bold**, *italic*, `code`, [text](url),
 * "-" and "*" unordered lists, "1." ordered lists, "> " blockquotes,
 * "| col |" tables, "---" hr, "```" fenced code blocks, blank-line
 * paragraphs. Also fixes UTF-8 mojibake that occasionally leaks from
 * the runtime.
 */

// Sentinel for stashed fenced code blocks. ESC (\x1b) won't appear in
// normal text so it can't collide with anything an agent writes.
const CB_RE = /\x1bCB(\d+)\x1b/g;
const cbMarker = (i) => `\x1bCB${i}\x1b`;

export function renderMarkdown(text) {
  if (!text) return "";

  text = text
    .replace(/â€”/g, "—")
    .replace(/â€“/g, "—")
    .replace(/â€™/g, "’");

  // Pull fenced code blocks out first so their contents don't get
  // chewed up by the block/inline transforms. Restore at the end.
  const codeBlocks = [];
  text = text.replace(
    /```([a-zA-Z0-9_+\-]*)\n([\s\S]*?)```/g,
    (_m, lang, code) => {
      const escaped = escapeHtml(code).replace(/\n$/, "");
      codeBlocks.push(
        `<pre><code class="lang-${lang || "text"}">${escaped}</code></pre>`,
      );
      return cbMarker(codeBlocks.length - 1);
    },
  );

  const html = text
    .split(/\n{2,}/)
    .map((block) => renderBlock(block.replace(/^\n+|\n+$/g, "")))
    .filter(Boolean)
    .join("\n");

  return html.replace(CB_RE, (_m, i) => codeBlocks[Number(i)]);
}

function renderBlock(block) {
  if (!block) return "";
  if (/^\x1bCB\d+\x1b$/.test(block)) return block;

  const heading = block.match(/^(#{1,6}) (.+)$/);
  if (heading) {
    const level = heading[1].length;
    return `<h${level}>${renderInline(heading[2])}</h${level}>`;
  }

  if (/^[-*_]{3,}$/.test(block)) return "<hr>";

  const lines = block.split("\n");

  if (lines.every((l) => /^>\s?/.test(l))) {
    const inner = lines.map((l) => l.replace(/^>\s?/, "")).join(" ");
    return `<blockquote>${renderInline(inner)}</blockquote>`;
  }

  if (lines.every((l) => /^[-*]\s+/.test(l))) {
    const items = lines
      .map((l) => `<li>${renderInline(l.replace(/^[-*]\s+/, ""))}</li>`)
      .join("");
    return `<ul>${items}</ul>`;
  }

  if (lines.every((l) => /^\d+\.\s+/.test(l))) {
    const items = lines
      .map((l) => `<li>${renderInline(l.replace(/^\d+\.\s+/, ""))}</li>`)
      .join("");
    return `<ol>${items}</ol>`;
  }

  // Only treat as a table when row 2 is the |---|---| separator. Any
  // 2-line paragraph that happens to include pipes would otherwise get
  // misread as a table.
  if (
    lines.length >= 2 &&
    lines.every((l) => l.includes("|")) &&
    /^[\s|:\-]+$/.test(lines[1]) &&
    lines[1].includes("-")
  ) {
    const rows = lines
      .map((l) =>
        l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()),
      )
      .filter((cells) => !cells.every((c) => /^[-:]*$/.test(c)));
    const tableHtml = rows
      .map(
        (cells) =>
          `<tr>${cells.map((c) => `<td>${renderInline(c)}</td>`).join("")}</tr>`,
      )
      .join("");
    return `<table>${tableHtml}</table>`;
  }

  return `<p>${renderInline(block).replace(/\n/g, "<br>")}</p>`;
}

function renderInline(text) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*\n]*?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/`([^`\n]+)`/g, (_m, code) => `<code>${escapeHtml(code)}</code>`)
    .replace(
      /\[([^\]]+)\]\(([^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noreferrer">$1</a>',
    );
}

function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
