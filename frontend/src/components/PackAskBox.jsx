import { useState } from "react";
import { Send } from "lucide-react";
import "./PackAskBox.css";

/**
 * Inline "Ask the pack" input. Lives at the top of PackStatusPane
 * on Home. Doesn't do the dispatch itself — hands the typed text
 * off to AskMaiko via the open-ask-pack event so the full panel
 * opens with the query pre-filled. Keeps dispatch state in one
 * place (the panel) and lets this input stay tiny and contextual.
 */
export default function PackAskBox() {
  const [text, setText] = useState("");

  const submit = () => {
    const t = text.trim();
    if (!t) return;
    window.dispatchEvent(
      new CustomEvent("open-ask-pack", { detail: { text: t } })
    );
    setText("");
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="pack-ask-box">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        placeholder="Ask the pack…"
        className="pack-ask-input"
      />
      <button
        type="button"
        className="pack-ask-send"
        onClick={submit}
        disabled={!text.trim()}
        title="Hand off to an agent"
      >
        <Send size={12} />
      </button>
    </div>
  );
}
