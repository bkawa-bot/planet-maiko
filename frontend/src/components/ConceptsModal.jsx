import { X, Inbox, CheckSquare, Bot, BookOpen, Brain, Zap } from "@icons";
import ModalPortal from "./ModalPortal";
import "./ConceptsModal.css";

/**
 * Mental-model cheatsheet — the vocabulary a new user picks up over
 * the first few days. Surfaced as a button in Settings so anyone who
 * skipped the setup wizard (or comes back to Maiko after a break)
 * can re-read the terms in one place. Intentionally verbose — the
 * reader is here because they lost the thread, not because they
 * wanted brevity.
 */
export default function ConceptsModal({ onClose }) {
  return (
    <ModalPortal>
    <div className="modal-overlay" onClick={onClose}>
      <div className="concepts-modal" onClick={(e) => e.stopPropagation()}>
        <div className="concepts-header">
          <h3>How Planet Maiko hangs together</h3>
          <button className="btn-ghost" onClick={onClose} title="Close"><X size={14} /></button>
        </div>

        <Concept icon={<Inbox size={14} />} title="Pupdates" subtitle="things to notice">
          Notifications from your pollers (GitHub, Linear, Calendar,
          PagerDuty) plus internal events. Surface on <strong>Home</strong>
          as memos. Some are "action" (block on you), most are
          "activity" (ambient). The brain cycle triages them into
          tasks or ambient noise.
        </Concept>

        <Concept icon={<CheckSquare size={14} />} title="Tasks" subtitle="things to finish">
          Actual work items. Created by hand, auto-generated from
          actionable pupdates, or spawned by <strong>Automation</strong>
          rules. Live on the <strong>Tasks</strong> page. An agent can
          be assigned to a task.
        </Concept>

        <Concept icon={<Bot size={14} />} title="Agents" subtitle="your pack">
          Characters with a name, an avatar, a role (coding / review /
          investigation / cartographer), and a scope (a repo or
          "global"). Run in isolated git worktrees with their own
          CLAUDE.md so they don't trample your work. Profiles live
          on the <strong>Pack</strong> page.
        </Concept>

        <Concept icon={<BookOpen size={14} />} title="Insights" subtitle="tribal knowledge">
          Short onboarding-style notes — <em>"Use IntelliJ for tests,
          the CLI runner is broken"</em>. Written by agents during
          work or typed by you. Live in the <strong>Pack Insights
          library</strong> on the Pack page. Approved insights get
          injected verbatim into every new agent's CLAUDE.md.
        </Concept>

        <Concept icon={<Brain size={14} />} title="Learnings" subtitle="coding rules for review agents">
          Rule-shaped knowledge extracted from PR review comments,
          agent feedback, and the Pack Insights ritual. Live on the
          <strong> Knowledge</strong> page. Review agents pull the
          ones relevant to the diff they're reading via
          <code> maiko rules-relevant</code> — that's how your
          team's accumulated taste shows up in agent reviews.
        </Concept>

        <Concept icon={<Zap size={14} />} title="Automations" subtitle="when X happens, do Y">
          Small when→then rules. "When a PR I'm tagged on goes
          stale, leave me a memo." "When a coding agent finishes,
          ping me in chat." A handful of defaults ship pre-wired;
          user-authored ones (and any <strong>Specialties</strong>,
          which are pre-built role playbooks an agent adopts for a
          specific kind of work) live on the
          <strong> Automations</strong> page.
        </Concept>

        <div className="concepts-divider" />

        <h4>The rhythm</h4>
        <p className="concepts-paragraph">
          Pollers run in the background and produce <em>pupdates</em>.
          The brain cycle triages them — automation rules handle the
          routine cases, the LLM handles the rest, deciding which
          become <em>tasks</em>. Agents pick up tasks and work in
          isolated worktrees. At end of day, the
          <strong> Pack Insights campfire</strong> on the Pack page
          gathers active agents; each shares feedback (→
          <em> learnings</em>) and insights (→ <em>playbook</em>).
          Review agents retrieve relevant learnings when reading a
          PR, so the pack gets better at your repos over time.
        </p>

        <h4>Status you can glance at</h4>
        <ul className="concepts-bullets">
          <li><strong>Home</strong> — the overview narrative, today's calendar, what's waiting on you.</li>
          <li><strong>Topbar health dot</strong> — pollers + brain cycle + last backup.</li>
          <li><strong>Topbar power button</strong> — end-of-day shutdown ritual.</li>
          <li><strong>Pack dock (left edge)</strong> — every agent currently running; hover for their last status.</li>
        </ul>

        <div className="concepts-footer">
          <button className="btn btn-primary" onClick={onClose}>Got it</button>
        </div>
      </div>
    </div>
    </ModalPortal>
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
