import { X, Inbox, CheckSquare, Bot, BookOpen, Brain, Zap, Compass } from "lucide-react";
import "./ConceptsModal.css";

/**
 * Mental-model cheatsheet — mirrors the README's "Mental model" section.
 *
 * Surfaced as a button in Settings so a user who skipped the setup
 * wizard (or comes back to Maiko after a break) can re-read the
 * vocabulary in one place. Intentionally verbose — the reader is
 * here because they lost the thread, not because they wanted
 * brevity.
 */
export default function ConceptsModal({ onClose }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="concepts-modal" onClick={(e) => e.stopPropagation()}>
        <div className="concepts-header">
          <h3>How Planet Maiko hangs together</h3>
          <button className="btn-ghost" onClick={onClose} title="Close"><X size={14} /></button>
        </div>

        <Concept icon={<Inbox size={14} />} title="Pupdates" subtitle="things to notice">
          Notifications from your pollers (GitHub, Linear, Slack,
          Calendar) plus internal events. Land in the <strong>Inbox</strong>.
          Some are "action" (block on you), most are "activity"
          (ambient). The brain cycle triages them into tasks,
          insights, or ambient noise.
        </Concept>

        <Concept icon={<CheckSquare size={14} />} title="Tasks" subtitle="things to finish">
          Actual work items. Created by hand, auto-generated from
          actionable pupdates, or auto-spawned (e.g. when the
          correlator fires an incident). Live on the <strong>Tasks</strong>
          page. An agent can be assigned to a task.
        </Concept>

        <Concept icon={<Bot size={14} />} title="Agents" subtitle="your pack">
          Personas with a role (coding / review / investigation /
          cartographer) and scope (a repo or "global"). Run in git
          worktrees with their own CLAUDE.md. Profiles live on the
          <strong> Agents</strong> page.
        </Concept>

        <Concept icon={<BookOpen size={14} />} title="Insights" subtitle="tribal knowledge">
          Short onboarding-style notes — <em>"Use IntelliJ for tests,
          the CLI runner is broken"</em>. Written by agents during work
          or typed by you. Live in the <strong>Pack Insights library</strong>
          on the Agents page. Approved insights get injected
          verbatim into every new agent's CLAUDE.md.
        </Concept>

        <Concept icon={<Brain size={14} />} title="Learnings" subtitle="coding rules surfaced to future agents">
          Rule-shaped knowledge, extracted from PR review comments,
          agent feedback, and the Pack Insights ritual. Live on the
          <strong> Knowledge</strong> page. Surface to future agents at
          task kickoff via <code>maiko rules-relevant</code>.
        </Concept>

        <Concept icon={<Zap size={14} />} title="Automations" subtitle="things Maiko runs for you">
          Prompt templates that run on demand or a schedule. Custom
          user-authored ones live on the <strong>Automations</strong> page;
          scheduled briefings (morning brief, evening wrap, etc.)
          live under <strong>Settings → Scheduled Briefings</strong>.
        </Concept>

        <Concept icon={<Compass size={14} />} title="Goals" subtitle="things the pack watches for you">
          Standing intents an agent holds — <em>"keep this repo's
          overview current"</em>, <em>"nudge me when these rules are
          worth training"</em>. Fire as <em>proposals</em> when their
          condition holds, so you approve what runs. Seeded from your
          configured repos and proposed by gap detectors. Visible
          on each profile's card; pause/resume per goal.
          <br/><br/>
          <strong>Goals vs Automations:</strong> automations run the
          same template on demand or a cadence; goals watch a
          condition and only nudge when it's met. An automation says
          "run morning brief at 8am"; a goal says "tell me when an
          agent hasn't checked in for a week."
        </Concept>

        <div className="concepts-divider" />

        <h4>The rhythm</h4>
        <p className="concepts-paragraph">
          Pollers run in the background and produce <em>pupdates</em>. The
          brain cycle triages pupdates — free rules handle the easy
          ones; the LLM handles the rest, deciding which become
          <em> tasks</em>. Agents pick up tasks and work in isolated
          worktrees. At end of day, the <strong>Pack Insights campfire</strong>
          on the Agents page gathers every active agent; each one
          shares feedback (→ <em>learnings</em>) and insights (→ <em>
          playbook</em>). Future agents retrieve relevant learnings
          on task kickoff so the pack gets better at your repos over
          time.
        </p>

        <h4>Status you can glance at</h4>
        <ul className="concepts-bullets">
          <li><strong>Home</strong> — scene, morning brief, today's activity, what's waiting on you.</li>
          <li><strong>Topbar health dot</strong> — pollers + brain cycle + last backup.</li>
          <li><strong>Topbar power button</strong> — end-of-day shutdown ritual.</li>
        </ul>

        <div className="concepts-footer">
          <button className="btn btn-primary" onClick={onClose}>Got it</button>
        </div>
      </div>
    </div>
  );
}


function Concept({ icon, title, subtitle, children }) {
  return (
    <div className="concept-item">
      <div className="concept-head">
        <span className="concept-icon">{icon}</span>
        <span className="concept-title">{title}</span>
        <span className="concept-subtitle">— {subtitle}</span>
      </div>
      <p className="concept-body">{children}</p>
    </div>
  );
}
