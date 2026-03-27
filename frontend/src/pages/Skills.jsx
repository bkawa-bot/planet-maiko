import { useEffect, useState } from "react";
import { api } from "../api/client";
import {
  Wand2, Sunrise, Brain, Coffee, Search, GitFork,
  Rocket, Clipboard, X, Loader,
} from "lucide-react";
import "./Skills.css";

const SKILL_ICONS = {
  "morning-brief": Sunrise,
  "brainstorm": Brain,
  "eod-summary": Coffee,
  "investigate": Search,
  "repo-analysis": GitFork,
};

export default function Skills() {
  const [skills, setSkills] = useState([]);
  const [selected, setSelected] = useState(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);

  useEffect(() => {
    api.getSkills().then(setSkills).catch(console.error);
  }, []);

  const runSkill = async (name) => {
    setRunning(true);
    setResult(null);
    try {
      const pupdates = await api.getPupdates();
      const tasks = await api.getTasks();
      const res = await api.runSkill(name, {
        context: {
          pupdates: JSON.stringify(pupdates.slice(0, 15), null, 2),
          tasks: JSON.stringify(tasks.slice(0, 15), null, 2),
          calendar: "[]", query: "", context: "",
        },
      });
      setResult(res);
    } catch (err) {
      setResult({ success: false, error: err.message });
    }
    setRunning(false);
  };

  return (
    <div className="skills-page">
      <h2><Wand2 size={18} /> Skills</h2>

      <div className="skills-grid">
        {skills.map((s) => {
          const Icon = SKILL_ICONS[s.name] || Wand2;
          return (
            <div key={s.name} className="skill-card card" onClick={() => setSelected(s)}>
              <Icon size={28} className="skill-icon" />
              <div className="skill-name">{s.name.replace(/-/g, " ")}</div>
              <div className="skill-desc">{s.description}</div>
              <div className="skill-meta">
                <code className="skill-cmd">/maiko-{s.name}</code>
              </div>
            </div>
          );
        })}
      </div>

      {/* Modal */}
      {selected && (
        <div className="modal-overlay" onClick={() => { setSelected(null); setResult(null); }}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              {(() => { const Icon = SKILL_ICONS[selected.name] || Wand2; return <Icon size={16} />; })()}
              <span>{selected.name.replace(/-/g, " ")}</span>
              <button className="btn btn-sm" onClick={() => { setSelected(null); setResult(null); }} style={{ marginLeft: "auto" }}>
                <X size={12} />
              </button>
            </div>
            <div className="modal-body">
              <p className="modal-desc">{selected.description}</p>
              <div className="modal-cmd">
                <code>/maiko-{selected.name}</code>
              </div>
              <div className="modal-actions">
                <button className="btn btn-primary" onClick={() => runSkill(selected.name)} disabled={running}>
                  {running ? <><Loader size={12} className="spin" /> Running...</> : <><Rocket size={12} /> Run in Maiko Session</>}
                </button>
                <button className="btn" onClick={() => navigator.clipboard.writeText(`/maiko-${selected.name}`)}>
                  <Clipboard size={12} /> Copy Command
                </button>
              </div>
              {result && (
                <div className={`skill-result ${result.success ? "" : "error"}`}>
                  {result.success ? (
                    <pre className="skill-output">{result.output}</pre>
                  ) : (
                    <div className="skill-error">{result.error}</div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
