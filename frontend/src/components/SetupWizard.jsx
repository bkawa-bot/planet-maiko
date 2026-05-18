import { useState } from "react";
import {
  Home as HomeIcon, FolderOpen, Brain, MapPin,
  GitBranch, Bot, Sparkles, Rocket, PawPrint, Zap,
} from "@icons";
import { api } from "../api/client";

const TOTAL_STEPS = 10;

/**
 * First-run setup wizard. Shown on Home when config.setup_complete is
 * false. Collects name + GitHub + repos + location, then walks through a
 * short tour of Home / Pack / Knowledge / Automations before marking
 * setup done.
 *
 * Props:
 *   onComplete — () => void, called after config is saved. Home reloads.
 */
export default function SetupWizard({ onComplete }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [repos, setRepos] = useState([]);
  const [repoRoots, setRepoRoots] = useState([]);
  const [discovering, setDiscovering] = useState(false);
  const [discoverError, setDiscoverError] = useState(null);
  const [location, setLocation] = useState("");
  const [locationResolved, setLocationResolved] = useState("");
  const [latLon, setLatLon] = useState(null);

  const finishSetup = async () => {
    const config = {};
    if (name.trim()) config.user = { name: name.trim() };
    if (username) {
      config.github = { username, enabled: true, repos };
      if (repoRoots.length) config.github.repo_roots = repoRoots;
    }
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
        // Don't auto-advance: the repo-roots input lives on this same
        // step and the user still needs to fill it in.
        setRepos(result.repos);
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
            <img src="/sprites/maiko-greeting.png" alt="Maiko" className="setup-maiko-icon" />
            <h1>Welcome to Planet Maiko</h1>
            <p className="setup-sub"><em>Strange agents, strange world.</em></p>
            <p className="setup-sub">A quiet companion for your engineering work. Maiko watches your PRs, holds the messy in-flight things, and runs small coding agents in their own worktrees so they never trample what you're doing.</p>
            <button className="btn btn-primary" onClick={() => setStep(1)} style={{ marginTop: 16 }}>
              <Rocket size={14} /> Come in
            </button>
          </div>
        )}

        {/* Step 1: Your Name */}
        {step === 1 && (
          <div className="setup-step">
            <div className="setup-step-icon"><PawPrint size={28} /></div>
            <h3>What should I call you?</h3>
            <p>Maiko uses this name in the home greeting and the daily overview. First name or nickname is fine.</p>
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
            <h3>Hook into GitHub</h3>
            <p>Drop your GitHub username so Maiko can see your PRs and reviews. You'll need <code>gh auth login</code> set up first.</p>
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
            <h3>Which repos to watch</h3>
            <p>Auto-discover from your recent activity, or type them in. Coding agents make their own worktrees off your local clones, so Maiko needs to know where the clones live on disk.</p>
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
            <p style={{ marginTop: 16 }}>Where do the clones live? Use the full path, a leading <code>~</code> can be unreliable.</p>
            <input
              type="text"
              value={repoRoots.join(", ")}
              onChange={(e) => setRepoRoots(e.target.value.split(",").map(s => s.trim()).filter(Boolean))}
              placeholder="/Users/you/src, /Users/you/code"
            />
            <div className="setup-actions">
              <button className="setup-skip" onClick={() => setStep(2)}>Back</button>
              <button className="btn btn-primary" onClick={() => setStep(4)}>Next</button>
            </div>
          </div>
        )}

        {/* Step 4: Location */}
        {step === 4 && (
          <div className="setup-step">
            <div className="setup-step-icon"><MapPin size={28} /></div>
            <h3>Where are you?</h3>
            <p>For live weather on your dashboard. Clouds drift across the page when it's overcast, rain falls when it's stormy, stars come out at night.</p>
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

        {/* Step 5: Tour — Home + Memos */}
        {step === 5 && (
          <div className="setup-step setup-step-centered">
            <div className="setup-step-icon tour-icon"><HomeIcon size={36} /></div>
            <h3>Home is where the memos land</h3>
            <p>PR pings, calendar events, agent updates, things waiting on you — they all surface as memos on Home. The overview pane up top is Maiko's narrative for the day; the feed below is everything else.</p>
            <p className="setup-detail">Click any memo to act on it. Dismiss the ones that don't need you, or ask Maiko to look into something with the floating Ask box.</p>
            <div className="setup-actions">
              <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
              <button className="btn btn-primary" onClick={() => setStep(6)}>Next</button>
            </div>
          </div>
        )}

        {/* Step 6: Tour — The Pack */}
        {step === 6 && (
          <div className="setup-step setup-step-centered">
            <div className="setup-step-icon tour-icon"><Bot size={36} /></div>
            <h3>Meet the Pack</h3>
            <p>Your agents are pups with personalities. Each one runs in its own isolated git worktree off your local clones, so they never trample what you're working on.</p>
            <p className="setup-detail">Stop one mid-flight and its worktree sticks around — revive it from the Pack page and it picks up where it left off.</p>
            <div className="setup-actions">
              <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
              <button className="btn btn-primary" onClick={() => setStep(7)}>Next</button>
            </div>
          </div>
        )}

        {/* Step 7: Tour — Knowledge */}
        {step === 7 && (
          <div className="setup-step setup-step-centered">
            <div className="setup-step-icon tour-icon"><Brain size={36} /></div>
            <h3>Knowledge</h3>
            <p>Maiko reads your team's PR review comments and notices the patterns — things you tend to flag, conventions you keep correcting. Approved patterns get pulled in when an agent reviews a PR, so the feedback aligns with your team's style.</p>
            <p className="setup-detail">Visit <strong>Knowledge</strong> and run <strong>Backfill from PRs</strong> to scan your history. Approve the ones that match your team; the rest stays out of the way.</p>
            <div className="setup-actions">
              <button className="setup-skip" onClick={finishSetup}>Skip Tour</button>
              <button className="btn btn-primary" onClick={() => setStep(8)}>Next</button>
            </div>
          </div>
        )}

        {/* Step 8: Tour — Automations + Specialties */}
        {step === 8 && (
          <div className="setup-step setup-step-centered">
            <div className="setup-step-icon tour-icon"><Zap size={36} /></div>
            <h3>Automations</h3>
            <p>Small when→then rules that let Maiko handle the boring stuff. "When a PR I'm tagged on goes stale, leave me a memo." "When a coding agent finishes, ping me in chat."</p>
            <p className="setup-detail">A handful of defaults ship pre-wired — open <strong>Automations</strong> to tweak them or write your own. The same page is where <strong>Specialties</strong> live: pre-built playbooks an agent can follow for specific kinds of work (triage, analysis, brainstorming).</p>
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
            <h3>You're settled in</h3>
            <p>A few places to wander next:</p>
            <ul className="setup-checklist">
              <li><strong>Connect integrations</strong> — Linear, Calendar, and others live under Settings.</li>
              <li><strong>Backfill from PRs</strong> — head to Knowledge so Maiko can read your team's review style.</li>
              <li><strong>Spawn an agent</strong> — visit the Pack and click New Agent. Assign it a task from the Tasks page.</li>
              <li><strong>Pick a theme</strong> — open the palette in the topbar (gear menu) or build your own under Themes.</li>
              <li><strong>Gather the pack at the campfire</strong> — Pack → Pack Insights kicks off the end-of-day gathering where agents share what they noticed.</li>
              <li><strong>Settle in for the night</strong> — the power button in the topbar tidies up the day's pupdates, stale worktrees, and dismissed memos.</li>
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
