import { useEffect, useState } from "react";
import { api } from "../api/client";
import { showToast } from "./Toast";
import { ChevronDown, ChevronRight, Loader, Rocket, Save } from "lucide-react";

// Digest-style skills: run on a schedule, produce a report. These used
// to live on the Skills page as cards. Surface them here where the
// schedule + prompt are the real configuration surface.
const DIGEST_SKILL_IDS = [
  "morning-brief",
  "brainstorm",
  "evening-wrap",
  "checkin",
  "plan",
  "team",
  "verify",
];

const SCHEDULE_OPTIONS = [
  { value: "", label: "Manual only" },
  { value: "60", label: "Every hour" },
  { value: "360", label: "Every 6 hours" },
  { value: "720", label: "Every 12 hours" },
  { value: "1440", label: "Daily" },
];

function BriefingRow({ skill, onChanged }) {
  const [schedule, setSchedule] = useState(
    skill.schedule_interval_minutes ? String(skill.schedule_interval_minutes) : ""
  );
  const [promptOpen, setPromptOpen] = useState(false);
  const [prompt, setPrompt] = useState(skill.prompt || "");
  const [savingPrompt, setSavingPrompt] = useState(false);
  const [running, setRunning] = useState(false);

  const handleScheduleChange = async (e) => {
    const next = e.target.value;
    setSchedule(next);
    try {
      await api.updateSkill(skill.id, {
        schedule_interval_minutes: next ? parseInt(next) : null,
      });
      showToast(next ? `${skill.name} will run ${SCHEDULE_OPTIONS.find(o => o.value === next)?.label.toLowerCase()}` : `${skill.name} set to manual`, "normal");
      onChanged && onChanged();
    } catch (err) {
      showToast(err.message || "Couldn't update schedule", "high");
    }
  };

  const savePrompt = async () => {
    setSavingPrompt(true);
    try {
      await api.updateSkill(skill.id, { prompt });
      showToast(`${skill.name} prompt saved`, "normal");
      onChanged && onChanged();
    } catch (err) {
      showToast(err.message || "Couldn't save prompt", "high");
    }
    setSavingPrompt(false);
  };

  const runNow = async () => {
    setRunning(true);
    showToast(`Running ${skill.name}...`, "normal");
    try {
      // Same context shape the Skills page used to pass — keeps default
      // prompts that reference {pupdates}/{tasks} working unchanged.
      const [pupdates, tasks] = await Promise.all([
        api.getPupdates().catch(() => []),
        api.getTasks().catch(() => []),
      ]);
      const res = await api.runSkill(skill.id, {
        context: {
          pupdates: JSON.stringify((pupdates || []).slice(0, 15)),
          tasks: JSON.stringify((tasks || []).slice(0, 15)),
          calendar: "[]", query: "", context: "",
        },
      });
      if (res?.success) {
        showToast(`${skill.name} done ✨`, "normal");
      } else {
        showToast(res?.error || `${skill.name} had trouble`, "high");
      }
    } catch (err) {
      showToast(err.message || "Run failed", "high");
    }
    setRunning(false);
  };

  return (
    <div className="briefing-row">
      <div className="briefing-header">
        <div className="briefing-info">
          <div className="briefing-name">{skill.name}</div>
          <div className="briefing-desc">{skill.description}</div>
        </div>
        <div className="briefing-controls">
          <select value={schedule} onChange={handleScheduleChange} className="routing-select">
            {SCHEDULE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <button
            className="btn btn-sm"
            onClick={runNow}
            disabled={running}
            title="Run this briefing once, right now"
          >
            {running ? <Loader size={10} className="spin" /> : <Rocket size={10} />}
            {running ? " Running…" : " Run now"}
          </button>
        </div>
      </div>
      <button
        className="btn-ghost briefing-prompt-toggle"
        onClick={() => setPromptOpen((v) => !v)}
      >
        {promptOpen ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
        <span>Edit prompt</span>
      </button>
      {promptOpen && (
        <div className="briefing-prompt">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={10}
            style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
          />
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <button
              className="btn btn-sm btn-primary"
              onClick={savePrompt}
              disabled={savingPrompt}
            >
              {savingPrompt ? <Loader size={10} className="spin" /> : <Save size={10} />}
              {savingPrompt ? " Saving…" : " Save prompt"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ScheduledBriefings() {
  const [skills, setSkills] = useState(null);

  const fetchSkills = () => {
    api.getSkills().then(setSkills).catch(() => setSkills([]));
  };

  useEffect(() => {
    fetchSkills();
  }, []);

  if (skills === null) {
    return <div className="setup-hint">Loading briefings…</div>;
  }

  const digests = skills.filter((s) => DIGEST_SKILL_IDS.includes(s.id));

  if (digests.length === 0) {
    return (
      <div className="setup-hint">
        No scheduled briefings available yet — defaults seed on first server start.
      </div>
    );
  }

  return (
    <div className="briefings-list">
      <div className="setup-hint">
        Maiko runs these on a schedule and saves the output. Morning Brief
        surfaces on Home; the others land in your Inbox. Tune the prompt
        to change what Maiko actually writes.
      </div>
      {digests.map((s) => (
        <BriefingRow key={s.id} skill={s} onChanged={fetchSkills} />
      ))}
    </div>
  );
}
