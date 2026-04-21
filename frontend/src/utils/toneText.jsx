/**
 * ToneText — render a string that may contain inline tone markup.
 *
 * Maiko's home-overview prompt lets her wrap phrases in [tone]...[/tone]
 * tags so a single line can shift typeface mid-sentence. This helper
 * parses those tags into safe React spans (no dangerouslySetInnerHTML),
 * and degrades gracefully when an unknown tone tag appears — the
 * markup is stripped, the content is kept as plain text.
 *
 * Allowed tones live alongside the CSS classes in index.css.
 */

const ALLOWED_TONES = new Set(["glitch", "cozy", "dramatic", "whisper"]);
const TONE_REGEX = /\[(\w+)\]([\s\S]*?)\[\/\1\]/g;


export default function ToneText({ text, as: Tag = "span", className }) {
  if (!text) return null;
  // No tags? Fast path — render plain text unchanged so we don't pay
  // regex + array overhead for the 95% case.
  if (!text.includes("[")) {
    return <Tag className={className}>{text}</Tag>;
  }

  const parts = [];
  let lastIndex = 0;
  let match;
  TONE_REGEX.lastIndex = 0;
  while ((match = TONE_REGEX.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const tone = match[1];
    const content = match[2];
    if (ALLOWED_TONES.has(tone)) {
      parts.push(
        <span key={match.index} className={`tone-${tone}`}>{content}</span>
      );
    } else {
      // Unknown tag — drop the wrapper, keep the text.
      parts.push(content);
    }
    lastIndex = TONE_REGEX.lastIndex;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return <Tag className={className}>{parts}</Tag>;
}
