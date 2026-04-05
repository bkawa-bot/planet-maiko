import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { Brain, Shield, CloudSun, Bot, Bug } from "lucide-react";
import "./Footer.css";

export default function Footer() {
  const [brainStatus, setBrainStatus] = useState(null);
  const [focusState, setFocusState] = useState("available");
  const [scene, setScene] = useState(null);
  const [agentCount, setAgentCount] = useState(0);
  const navigate = useNavigate();

  useEffect(() => {
    const refresh = async () => {
      try {
        const [brain, focus, sc, profiles] = await Promise.all([
          api.getBrainStatus().catch(() => null),
          api.getFocus().catch(() => null),
          api.getScene().catch(() => null),
          api.getProfiles().catch(() => []),
        ]);
        if (brain) setBrainStatus(brain);
        if (focus) setFocusState(focus.current_state || "available");
        if (sc?.context?.weather) setScene(sc.context);
        setAgentCount(profiles.length);
      } catch (err) { /* ignore */ }
    };
    refresh();
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, []);

  return (
    <footer className="footer">
      <div className="footer-section" onClick={() => navigate("/knowledge")} title="Brain status">
        <span className="footer-dot brain" />
        <Brain size={10} />
        <span>{brainStatus?.cycle_count ? `${brainStatus.cycle_count} cycles` : "active"}</span>
      </div>

      <div className="footer-section">
        <Shield size={10} />
        <span className={`footer-focus ${focusState}`}>{focusState.replace("_", " ")}</span>
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

      <a className="footer-section footer-bug" href="https://github.com/bkawa-bot/planet-maiko/issues/new" target="_blank" rel="noreferrer" title="Report a bug">
        <Bug size={10} />
        <span>Report Bug</span>
      </a>
    </footer>
  );
}
