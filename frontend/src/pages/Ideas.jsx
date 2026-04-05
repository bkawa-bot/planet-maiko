import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import {
  Lightbulb, Search, FileText, Folder, X, RefreshCw,
  Brain, Play, Loader,
} from "lucide-react";
import "./Suggestions.css";
import "./Brainstorm.css";

export default function Ideas() {
  const [tab, setTab] = useState("suggestions");

  // Suggestions state
  const [suggestions, setSuggestions] = useState([]);
  const [sugLoading, setSugLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [category, setCategory] = useState("all");

  // Brainstorm state
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState(null);

  const fetchSuggestions = async () => {
    try {
      const pupdates = await api.getPupdates();
      setSuggestions(pupdates.filter((p) => p.type === "suggestion"));
    } catch (err) { console.error(err); }
    setSugLoading(false);
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

  const runBrainstorm = async () => {
    setRunning(true);
    setResult(null);
    showToast("Maiko is thinking... 🧠", "normal");
    try {
      const pupdates = await api.getPupdates();
      const tasks = await api.getTasks();
      const res = await api.runSkill("brainstorm", {
        context: {
          pupdates: JSON.stringify(pupdates.slice(0, 20), null, 2),
          tasks: JSON.stringify(tasks.slice(0, 20), null, 2),
        },
      });
      setResult(res);
      setLastRun(new Date());
      showToast(res.success ? "Brainstorm complete! 💡" : "Brainstorm had trouble", res.success ? "normal" : "high");
    } catch (err) {
      setResult({ success: false, error: err.message, output: "" });
      showToast("Something went wrong", "high");
    }
    setRunning(false);
  };

  const categories = ["all", ...new Set(suggestions.map((s) => s.metadata?.category || "general"))];
  const filtered = category === "all" ? suggestions : suggestions.filter((s) => (s.metadata?.category || "general") === category);

  return (
    <div className="ideas-page">
      <div className="suggestions-hero">
        <Lightbulb size={48} className="hero-icon" />
        <div>
          <h2>Maiko's Ideas</h2>
          <p className="hero-sub">Things Maiko found, thought up, and brought back for you</p>
        </div>
      </div>

      <div className="inbox-tab-bar" style={{ marginBottom: 12 }}>
        <button className={`inbox-tab ${tab === "suggestions" ? "active" : ""}`} onClick={() => setTab("suggestions")}>
          <Lightbulb size={12} /> Suggestions
        </button>
        <button className={`inbox-tab ${tab === "brainstorm" ? "active" : ""}`} onClick={() => setTab("brainstorm")}>
          <Brain size={12} /> Brainstorm
        </button>
      </div>

      {tab === "suggestions" ? (
        <>
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
            <button className="btn" onClick={handleScan} disabled={scanning}>
              <RefreshCw size={12} className={scanning ? "spin" : ""} />
              {scanning ? " Scanning..." : " Run Scan"}
            </button>
          </div>

          {suggestions.length > 0 && (
            <div className="category-chips">
              {categories.map((c) => (
                <button key={c} className={`chip ${category === c ? "active" : ""}`} onClick={() => setCategory(c)}>
                  {c === "all" ? `All (${suggestions.length})` : c.replace(/_/g, " ")}
                </button>
              ))}
            </div>
          )}

          {sugLoading ? (
            <p className="page-empty">Loading...</p>
          ) : filtered.length === 0 ? (
            <div className="empty-state">
              <Lightbulb size={36} className="empty-icon" />
              <div className="empty-title">No suggestions yet!</div>
              <div className="empty-sub">Run a scan to find stuck PRs, stale tasks, and improvements</div>
              <button className="btn btn-primary" onClick={handleScan} style={{ marginTop: 12 }}>
                Run Scan
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
        </>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8, marginBottom: 12 }}>
            {lastRun && <span className="brainstorm-time">{lastRun.toLocaleTimeString()}</span>}
            <button className="btn" onClick={runBrainstorm} disabled={running}>
              {running ? <><Loader size={12} className="spin" /> Running...</> : <><RefreshCw size={12} /> Run Brainstorm</>}
            </button>
          </div>

          {!result && !running ? (
            <div className="empty-state">
              <Brain size={48} className="empty-icon" />
              <div className="empty-title">No brainstorm data yet</div>
              <div className="empty-sub">Run a brainstorm to analyze error trends, metrics, and find improvement opportunities</div>
              <button className="btn btn-primary" onClick={runBrainstorm} style={{ marginTop: 12 }}>
                <Play size={12} /> Run Brainstorm
              </button>
            </div>
          ) : running ? (
            <div className="brainstorm-loading">
              <Loader size={28} className="spin" style={{ color: "var(--pink)" }} />
              <p>Analyzing pupdates, tasks, and trends...</p>
              <p className="loading-sub">This may take a minute</p>
            </div>
          ) : result?.success ? (
            <div className="brainstorm-content card">
              <div className="md-content">{result.output}</div>
            </div>
          ) : (
            <div className="brainstorm-error card" style={{ borderLeft: "3px solid var(--urgent)" }}>
              <strong>Error:</strong> {result?.error}
            </div>
          )}
        </>
      )}
    </div>
  );
}
