import { useState, useEffect } from "react";
import { api } from "../api/client";
import "./Footer.css";

export default function Footer() {
  const [brainStatus, setBrainStatus] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(new Date());

  useEffect(() => {
    const refresh = async () => {
      try {
        setBrainStatus(await api.getBrainStatus());
        setLastRefresh(new Date());
      } catch (err) { /* ignore */ }
    };
    refresh();
    const interval = setInterval(refresh, 15000);
    return () => clearInterval(interval);
  }, []);

  return (
    <footer className="footer">
      <div className="footer-left">
        <span className="footer-dot" />
        <span>Auto-refresh: 15s</span>
      </div>
      <div className="footer-right">
        <span className="footer-indicator">
          <span className="sys-dot brain" /> Brain: {brainStatus?.cycle_count || 0} cycles
        </span>
        <span className="footer-sep">·</span>
        <span>Last: {lastRefresh.toLocaleTimeString()}</span>
      </div>
    </footer>
  );
}
