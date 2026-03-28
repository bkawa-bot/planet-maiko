import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Lightbulb, Search, FileText, Folder, Rocket, X, RefreshCw } from "lucide-react";
import "./Suggestions.css";

export default function Suggestions() {
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [category, setCategory] = useState("all");

  const fetchSuggestions = async () => {
    try {
      const pupdates = await api.getPupdates();
      setSuggestions(pupdates.filter((p) => p.type === "suggestion"));
    } catch (err) {
      console.error("Failed to load:", err);
    }
    setLoading(false);
  };

  useEffect(() => { fetchSuggestions(); }, []);

  const handleScan = async () => {
    setScanning(true);
    try {
      await api.runScan();
      await fetchSuggestions();
    } catch (err) { console.error(err); }
    setScanning(false);
  };

  const handleDismiss = async (id) => {
    await api.dismissPupdate(id);
    setSuggestions((prev) => prev.filter((s) => s.id !== id));
  };

  const categories = ["all", ...new Set(suggestions.map((s) => s.metadata?.category || "general"))];
  const filtered = category === "all" ? suggestions : suggestions.filter((s) => (s.metadata?.category || "general") === category);

  if (loading) return <p className="page-empty">Loading...</p>;

  return (
    <div className="suggestions-page">
      <div className="suggestions-hero">
        <Lightbulb size={48} className="hero-icon" />
        <div>
          <h2>Maiko's Suggestion Box</h2>
          <p className="hero-sub">Things Maiko found and brought back for you</p>
        </div>
        <button className="btn" onClick={handleScan} disabled={scanning} style={{ marginLeft: "auto" }}>
          <RefreshCw size={12} className={scanning ? "spin" : ""} />
          {scanning ? "Scanning..." : "Run Scan"}
        </button>
      </div>

      {suggestions.length > 0 && (
        <div className="category-chips">
          {categories.map((c) => (
            <button
              key={c}
              className={`chip ${category === c ? "active" : ""}`}
              onClick={() => setCategory(c)}
            >
              {c === "all" ? `All (${suggestions.length})` : c.replace(/_/g, " ")}
            </button>
          ))}
        </div>
      )}

      {filtered.length === 0 ? (
        <div className="empty-state">
          <Lightbulb size={36} className="empty-icon" />
          <div className="empty-title">No suggestions yet!</div>
          <div className="empty-sub">Run a scan or brainstorm to find improvements</div>
          <button className="btn btn-primary" onClick={handleScan} style={{ marginTop: 12 }}>
            Run Brainstorm
          </button>
        </div>
      ) : (
        <div className="toy-list card-list-container">
          {filtered.map((s) => (
            <div key={s.id} className="toy-card">
              <div className="toy-header">
                <Lightbulb size={14} />
                <span className="toy-title">{s.title}</span>
                {s.metadata?.estimated_effort && (
                  <span className={`effort-badge effort-${s.metadata.estimated_effort}`}>
                    {s.metadata.estimated_effort}
                  </span>
                )}
              </div>
              {s.body && <div className="rich-body">{s.body}</div>}
              <div className="toy-meta">
                <span className="toy-time">{new Date(s.timestamp).toLocaleDateString()}</span>
                {s.metadata?.category && (
                  <span className="tag">{s.metadata.category.replace(/_/g, " ")}</span>
                )}
                {s.tags?.filter((t) => t !== "suggestion").map((t) => (
                  <span key={t} className="tag">{t}</span>
                ))}
              </div>
              <div className="toy-actions">
                <button className="btn"><Search size={12} /> Investigate</button>
                <button className="btn"><FileText size={12} /> Linear Draft</button>
                <button className="btn"><Folder size={12} /> Project</button>
                <button className="btn btn-danger" onClick={() => handleDismiss(s.id)}>
                  <X size={12} /> Dismiss
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
