# Backlog

Brain-dump of small fixes and design questions. Nothing here blocks anything shipping today. Pick one at a time when you have energy; the rest will wait.

## UI clutter & polish

- **Weekend pill in the topbar.** Is it always there, or only showing on actual weekends? Feels like noise either way. Options: remove, gate to Sat/Sun only, or fold into the focus pill.
- **Duplicate "available" signal.** Shows in the topbar AND the bottom bar. Pick one, keep the topbar.
- **Pet Maiko widget is too big.** Currently a full home-sidebar widget. Idea: shrink to a discrete button in the bottom bar that opens the petting interaction.
- **Afternoon theme.** Gut it. (Keep morning, sunset, day, night, auto.)
- **Frosted panes.** Bump transparency a notch across `.frost-pane`.

## Agents page cleanup

- **Profile cards are ugly at scale.** People might have 30+ agents. Fixes:
  - Role sections (Coder / Reviewer / Investigator / Cartographer) should be collapsible
  - Card reduced to avatar + name + state dot, maybe one line of bio
  - Click opens full profile (stats, context set, recent tasks) in a modal
- **Too many buttons on active agent cards.** Review, Re-run, View Session, Chat, Timeline, Relaunch, Nudge is a lot. Nudge is probably redundant now that the wake orchestrator auto-wakes on heartbeat. Consider dropping it.

## Navigation & IA

- **"Ask Maiko" feels like an Amazon assistant.** Floating corner button reads too e-commerce. On Home, reframe as "Ask the pack" inside the Pack Status area — still Maiko answering, just in context instead of as a chatbot.
- **Combine Training into Knowledge.** They're two halves of the same pipeline (signals → learnings → training → adapters). Merging them makes the whole flow legible in one tab.

## Bugs to confirm

- **"Open" button on overview "also needs attention" items doesn't work right.** Investigate what it's supposed to do and fix.
- **Shutdown flow might not actually stop the service.** Blank progress bar says *"Maiko is shutting down"* but the process seems to keep running. Check `/api/system/shutdown` SIGTERM path and whether the frontend correctly detects completion.

## Design questions (not quick fixes)

- **Do investigation agents auto-run on high-priority poller alerts?** If the GitHub poller fires an error-spike or stale-deploy signal, what happens today? Gut: it becomes a pupdate, maybe surfaces in Pack Requests, but no auto-investigation — which means you could miss real fires while away. Worth designing: which alert types trigger an auto-investigation, what the agent does, how the user approves or dismisses.
- **What happens when an investigation agent finds something worth fixing?** Today it surfaces a report as a pupdate and the user decides. Could be smoother — e.g. `PROPOSAL:` blocks in the investigation output auto-spawn a queued coding task the user can approve or dismiss.
- **Skills feel disconnected from the pack.** Skills (morning brief, brainstorm, repo-analysis) are prompt templates running on a schedule, outside the agent system. Possible reframe: skills are agent-task templates, not their own category. Scheduled skills = scheduled agent tasks. A repo-analysis skill would be a cartographer agent's task, not a standalone skill run.

---

*Captured 2026-04-19 during a brain-dump session. Add more as you notice them; resolve them in whatever order feels good.*
