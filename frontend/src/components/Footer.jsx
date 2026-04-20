import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { Brain, CloudSun, Bot, Bug, Sparkles } from "lucide-react";
import { showToast } from "./Toast";
import FooterPendingPopover from "./FooterPendingPopover";
import PetMaikoFooter from "./PetMaikoFooter";
import "./Footer.css";


// Rendered in k-notation (1.2k) once the count crosses this threshold
// — a literal "3247 pending" chip is visually alarming and prone to
// overflow the footer row.
const COMPACT_THRESHOLD = 1000;

function _formatCount(n) {
  if (!n || n < COMPACT_THRESHOLD) return `${n}`;
  return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`;
}

export default function Footer() {
  const [brainStatus, setBrainStatus] = useState(null);
  const [scene, setScene] = useState(null);
  const [agentCount, setAgentCount] = useState(0);
  const [cycling, setCycling] = useState(false);
  const [showPendingPopover, setShowPendingPopover] = useState(false);
  const navigate = useNavigate();

  const refresh = async () => {
    try {
      const [brain, sc, profiles] = await Promise.all([
        api.getBrainStatus().catch(() => null),
        api.getScene().catch(() => null),
        api.getProfiles().catch(() => []),
      ]);
      if (brain) setBrainStatus(brain);
      if (sc?.context?.weather) setScene(sc.context);
      setAgentCount(profiles.length);
    } catch (err) { /* ignore */ }
  };

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 60000);
    return () => clearInterval(interval);
  }, []);

  const triggerCycle = async (e) => {
    // Stop the parent click from also navigating to /knowledge.
    e.stopPropagation();
    if (cycling) return;
    setCycling(true);
    showToast("Brain cycle running...", "normal");
    try {
      await api.runBrainCycle();
      showToast("Brain cycle done", "normal");
      refresh();
    } catch (err) {
      showToast(err.message || "Cycle failed", "high");
    } finally {
      setCycling(false);
    }
  };

  return (
    <footer className="footer">
      <div className="footer-section" onClick={() => navigate("/knowledge")} title="Brain status">
        <span className="footer-dot brain" />
        <Brain size={10} />
        <span>{brainStatus?.cycle_count ? `${brainStatus.cycle_count} cycles` : "active"}</span>
        {brainStatus?.pending && Object.values(brainStatus.pending).some(v => v > 0) && (
          <button
            className="footer-pending"
            title="Click for a breakdown"
            onClick={(e) => {
              e.stopPropagation();
              setShowPendingPopover((v) => !v);
            }}
          >
            {_formatCount(Object.values(brainStatus.pending).reduce((a, b) => a + b, 0))} pending
          </button>
        )}
        {showPendingPopover && (
          <FooterPendingPopover
            pending={brainStatus?.pending || {}}
            onClose={() => setShowPendingPopover(false)}
          />
        )}
        <button
          className="footer-cycle-btn"
          onClick={triggerCycle}
          disabled={cycling}
          title="Run a brain cycle now (route tasks, process pupdates, etc.)"
        >
          <Sparkles size={9} className={cycling ? "spin" : ""} />
        </button>
      </div>

      {scene?.temperature_f && (
        <div className="footer-section" onClick={() => navigate("/settings")} title="Weather">
          <CloudSun size={10} />
          <span>{scene.temperature_f}°F {scene.weather || ""}</span>
        </div>
      )}

      <div className="footer-section" onClick={() => navigate("/agents")} title="Agents">
        <Bot size={10} />
        <span>{agentCount} agent{agentCount !== 1 ? "s" : ""}</span>
      </div>

      <PetMaikoFooter />

      <a className="footer-section footer-bug" href="https://github.com/bkawa-bot/planet-maiko/issues/new" target="_blank" rel="noreferrer" title="Report a bug">
        <Bug size={10} />
        <span>Report Bug</span>
      </a>
    </footer>
  );
}
