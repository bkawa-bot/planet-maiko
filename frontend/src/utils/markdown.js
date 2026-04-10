/**
 * Lightweight markdown renderer. Used by Home.jsx (morning brief) and
 * Inbox.jsx (PR review results). Not a full implementation — just enough
 * for the LLM output we show in those modals.
 *
 * Supports: #/##/### headers, **bold**, *italic*, `code`, - lists,
 * 1. ordered lists, | tables |, --- hr, blank-line paragraphs.
 * Also fixes UTF-8 mojibake that sometimes comes back from the runtime.
 */
export function renderMarkdown(text) {
  if (!text) return "";
  return text
    // Fix UTF-8 mojibake that occasionally leaks through the runtime layer
    .replace(/\u00e2\u20ac\u201d/g, "\u2014")  // em dash
    .replace(/\u00e2\u20ac\u201c/g, "\u2014")  // en dash
    .replace(/\u00e2\u20ac\u2122/g, "\u2019")  // right-single-quote
    // Headings
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    // Inline
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, "<code>$1</code>")
    // Lists (both unordered and ordered)
    .replace(/^\- (.+)$/gm, "<li>$1</li>")
    .replace(/^(\d+)\. (.+)$/gm, "<li>$2</li>")
    .replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>")
    // Tables: |col|col|  (skips the |---|---| separator row)
    .replace(/\|(.+)\|/g, (match) => {
      const cells = match.split("|").filter((c) => c.trim());
      if (cells.every((c) => c.trim().match(/^[-:]+$/))) return "";
      return "<tr>" + cells.map((c) => `<td>${c.trim()}</td>`).join("") + "</tr>";
    })
    .replace(/(<tr>.*<\/tr>\n?)+/g, "<table>$&</table>")
    // Horizontal rule
    .replace(/^---$/gm, "<hr>")
    // Paragraphs (blank lines become </p><p>)
    .replace(/\n\n/g, "</p><p>")
    .replace(/^(?!<[hultop])(.+)$/gm, "<p>$1</p>")
    .replace(/<p><\/p>/g, "");
}
