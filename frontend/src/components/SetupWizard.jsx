import { useState } from "react";
import {
  Shield, Inbox as InboxIcon, FolderOpen, Brain, Palette,
  GitBranch, Bot, Sparkles, Rocket, PawPrint,
} from "lucide-react";
import { api } from "../api/client";

const TOTAL_STEPS = 10;

/**
 * First-run setup wizard. Shown on Home when config.setup_complete is
 * false. Collects name + GitHub + repos + location, then walks through a
 * short tour of Inbox / Focus / Agents / Knowledge before marking setup done.
 *
 * Props:
 *   onComplete — () => void, called after config is saved. Home reloads.
 */
export default function SetupWizard({ onComplete }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [repos, setRepos] = useState([]);
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState(null);
  const [location, setLocation] = useState("");
  const [locationResolved, setLocationResolved] = useState("");
  const [latLon, setLatLon] = useState(null);

  const finishSetup = async () => {
    const config = {};
    if (name.trim()) config.user = { name: name.trim() };
    if (username) config.github = { username, enabled: true, repos };
    if (latLon) config.scene = { latitude: latLon.lat, longitude: latLon.lon, location_name: locationResolved };
    config.setup_complete = true;
    await api.updateConfig(config);
    onComplete();
  };

  const handleDiscoverRepos = async () => {
    setDiscoverError(null);
    setDiscovering(true);
    try {
      await api.updateConfig({ github: { username, enabled: true } });
      const result = await api.discoverGithubRepos();
      if (result.repos?.length) {
        setRepos(result.repos);
        setTimeout(() => setStep(4), 800);
      } else {
        setDiscoverError({
          message: "No repos found for that username.",
          hint: "You can add them manually below, comma-separated.",
        });
      }
    } catch (e) {
      // Surface the structured error from the backend so the user
      // sees a real next step (install gh / run gh auth login) rather
      // than a silent failure that looks like Maiko is broken.
      const msg = e?.message || String(e);
      if (/gh CLI not found/i.test(msg)) {
        setDiscoverError({
          message: "GitHub CLI isn't installed.",
          hint: "Install it from cli.github.com, then come back.",
        });
      } else if (/isn't authenticated|auth login/i.test(msg)) {
        setDiscoverError({
          message: "GitHub CLI isn't logged in.",
          hint: "Open a terminal and run: gh auth login",
        });
      } else {
        setDiscoverError({ message: msg, hint: "You can still type repos manually below." });
      }
    }
    setDiscovering(false);
  };

  const handleLocationLookup = async () => {
    try {
      const resp = await fetch(`https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(location)}&count=1&language=en&format=json`);
      const data = await resp.json();
      if (data.results?.length) {
        const r = data.results[0];
        setLocationResolved(`${r.name}, ${r.admin1 || ""}`);
        setLatLon({ lat: r.latitude, lon: r.longitude });
      }
    } catch (e) {
      // Best effort — geocoding is optional
    }
  };

  return (
    <div className="home">
      <div className="setup-wizard">
        {/* Progress dots */}
        <div className="setup-progress">
          {Array.from({ length: TOTAL_STEPS }).map((_, i) => (
            <div key={i} className={`setup-dot ${i === step ? "active" : ""} ${i < step ? "done" : ""}`} />
          ))}
        </div>

        {/* Step 0: Welcome */}
        {step === 0 && (
          <div className="setup-step setup-step-centered">
            <img src="/icon.svg" alt="Maiko" className="setup-maiko-icon" />
            <h1>Welcome to Planet Maiko</h1>
            <p className="setup-sub">Your personal engineering companion. Maiko monitors your PRs, triages notifications, and orchestrates coding agents that learn from your team.</p>
            <button className="btn btn-primary" onClick={() => setStep(1)} style={{ marginTop: 16 }}>
              <Rocket size={14} /> Get Started
            </button>
          </div>
        )}

        {/* Step 1: Your Name */}
        {step === 1 && (
          <div className="setup-step">
            <div className="setup-step-icon"><PawPrint size={28} /></div>
            <h3>What should I call you?</h3>
            <p>Maiko uses this name to greet you and reference you in briefs. First name or nickname is fine.</p>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Brigitte"
              autoFocus
              onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) setStep(2); }}
            />
            <div className="setup-actions">
              <button className="setup-skip" onClick={() => setStep(2)}>Skip</button>
              <button className="btn btn-primary" onClick={() => setStep(2)} disabled={!name.trim()}>Next</button>
            </div>
          </div>
        )}

        {/* Step 2: GitHub */}
        {step === 2 && (
          <div className="setup-step">
            <div className="setup-step-icon"><GitBranch size={28} /></div>
            <h3>Connect GitHub</h3>
            <p>Enter your GitHub username so Maiko can monitor your PRs and reviews. Requires <code>gh auth login</code> first.</p>
            <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="your-github-username" />
            <div className="setup-actions">
              <button className="setup-skip" onClick={() => setStep(4)}>Skip</button>
              <button className="btn btn-primary" onClick={() => setStep(3)} disabled={!username}>Next</button>
            </div>
          </div>
        )}

        {/* Step 3: Repos */}
        {step === 3 && (
          <div className="setup-step">
            <div className="setup-step-icon"><FolderOpen size={28} /></div>
            <h3>Your Repos</h3>
            <p>Which repos should Maiko watch? Auto-discover from your recent activity, or type them manually.</p>
            <button className="btn btn-discover" onClick={handleDiscoverRepos} disabled={discovering}>
              {discovering ? "Discovering..." : "Auto-Discover Repos"}
            </button>
            <input
              type="text"
              value={repos.join(", ")}
              onChange={(e) => setRepos(e.target.value.split(",").map(s => s.trim()).filter(Boolean))}
              placeholder="org/repo1, org/repo2"
            />
            {repos.length > 0 && (
              <div className="setup-hint-good">Found {repos.length} repo(s)</div>
            )}
            {discoverError && (
              <div className="setup-hint-warn">
                <div>{discoverError.message}</div>
                {discoverError.hint && <div className="setup-hint-warn-sub">{discoverError.hint}</div>}
              </div>
            )}
            <div className="setup-actions">
              <button className="setup-skip" onClick={() => setStep(2)}>Back</button>
              <button className="btn btn-primary" onClick={() => setStep(4)}>Next</button>
            </div>
          </div>
        )}

        {/* Step 4: Location */}
        {step === 4 && (
          <div className="setup-step">
            <div className="setup-step-icon"><Palette size={28} /></div>
            <h3>Your Location</h3>
            <p>For live weather on your dashboard. Clouds drift across the page when it's overcast, rain falls when it's stormy.</p>
            <div className="setup-location-row">
              <input
                type="text"
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="Boston"
              />
              <button className="btn" onClick={handleLocationLookup}>Lookup</button>
            </div>
            {locationResolved && <div className="setup-hint-good">{locationResolved}</div>}
            <div className="setup-actions">
              <button className="setup-skip" onClick={() => setStep(5)}>Skip</button>
              <button className="btn btn-primary" onClick={() => setStep(5)}>Next</button>
            </div>
          </div>
        )}

        {/* Step 5: Tour — Inbox */}
        {step === 5 && (
          <div className="setup-step setup-step-centered">
            <div className="setup-step-icon tour-icon"><InboxIcon size={36} /></div>
            <h3>Your Inbox</h3>
            <p>All your notifications land here — PRs, calendar events, CI alerts, and whatever integrations you connect. Maiko triages them automatically.</p>
            <p className="setup-detail">Tabs filter by type. You can dismiss, create tasks, or have Maiko investigate with one click.</p>
            <div className="setup-actions">
              <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
              <button className="btn btn-primary" onClick={() => setStep(6)}>Next</button>
            </div>
          </div>
        )}

        {/* Step 6: Tour — Focus Mode */}
        {step === 6 && (
          <div className="setup-step setup-step-centered">
            <div className="setup-step-icon tour-icon"><Shield size={36} /></div>
            <h3>Focus Mode</h3>
            <p>Control which notifications reach you based on how deep in the zone you are. Find it in the top-right of the nav bar.</p>
            <ul className="setup-checklist">
              <li><strong>Available</strong> — everything comes through</li>
              <li><strong>Soft focus</strong> — only high priority and above</li>
              <li><strong>Deep focus</strong> — only critical and urgent</li>
              <li><strong>Away</strong> — minimal interruptions</li>
            </ul>
            <p className="setup-detail">Held notifications are collected and released as a digest when you switch back.</p>
            <div className="setup-actions">
              <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
              <button className="btn btn-primary" onClick={() => setStep(7)}>Next</button>
            </div>
          </div>
        )}

        {/* Step 7: Tour — Agents */}
        {step === 7 && (
          <div className="setup-step setup-step-centered">
            <div className="setup-step-icon tour-icon"><Bot size={36} /></div>
            <h3>Meet Your Agents</h3>
            <p>Agents are coding assistants that each carry a personalized set of learnings tuned for specific task types. They work in isolated git worktrees.</p>
            <p className="setup-detail">New agents ("pups") explore with random learnings. Through training, they specialize and rank up.</p>
            <div className="setup-actions">
              <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
              <button className="btn btn-primary" onClick={() => setStep(8)}>Next</button>
            </div>
          </div>
        )}

        {/* Step 8: Tour — Knowledge + Training */}
        {step === 8 && (
          <div className="setup-step setup-step-centered">
            <div className="setup-step-icon tour-icon"><Brain size={36} /></div>
            <h3>Knowledge + Training</h3>
            <p>Maiko learns coding patterns from your PR review comments. These get injected into agent briefs so they follow your team's conventions.</p>
            <p className="setup-detail">Use <strong>Knowledge &gt; Backfill from PRs</strong> to scan your history. Then <strong>Training</strong> to teach agents on real merged PRs.</p>
            <div className="setup-actions">
              <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
              <button className="btn btn-primary" onClick={() => setStep(9)}>Next</button>
            </div>
          </div>
        )}

        {/* Step 9: Tour — Done */}
        {step === 9 && (
          <div className="setup-step setup-step-centered">
            <div className="setup-step-icon tour-icon"><Sparkles size={36} /></div>
            <h3>You're All Set!</h3>
            <p>Here's what to do next:</p>
            <ul className="setup-checklist">
              <li><strong>Connect integrations</strong> — Go to Settings to add Linear, Calendar, or other services. Agent tools (Bash, Read, Edit, etc.) are pre-configured — customize in Settings &gt; Agent Preferences.</li>
              <li><strong>Backfill knowledge</strong> — Go to Knowledge and click "Backfill from PRs"</li>
              <li><strong>Create an agent</strong> — Visit Agents and click "New Agent"</li>
              <li><strong>Train it</strong> — Go to Training, pick a merged PR, and run a session</li>
            </ul>
            <button className="btn btn-primary" onClick={finishSetup} style={{ marginTop: 16 }}>
              <Rocket size={14} /> Let's go
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
