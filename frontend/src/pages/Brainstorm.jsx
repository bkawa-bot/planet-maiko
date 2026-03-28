import { useState, useEffect } from "react";
import { api } from "../api/client";
import { showToast } from "../components/Toast";
import { Brain, Play, Loader, RefreshCw } from "lucide-react";
import "./Brainstorm.css";

export default function Brainstorm() {
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState(null);

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

  return (
    <div className="brainstorm-page">
      <div className="brainstorm-header">
        <Brain size={18} />
        <h2>Brainstorm</h2>
        {lastRun && <span className="brainstorm-time">{lastRun.toLocaleTimeString()}</span>}
        <button className="btn" onClick={runBrainstorm} disabled={running} style={{ marginLeft: "auto" }}>
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
    </div>
  );
}
