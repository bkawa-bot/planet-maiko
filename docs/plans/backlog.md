# Backlog

Brain-dump of small fixes and design questions. Nothing here blocks anything shipping today. Pick one at a time when you have energy; the rest will wait.

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

## Done (captured 2026-04-19, shipped same day)

- ~~Afternoon theme — gutted~~ (`ad538d3`)
- ~~Frosted panes more transparent~~ (`ad538d3`)
- ~~Weekend pill — gated to Sat/Sun or when on~~ (`ea35edf`)
- ~~Duplicate "available" signal in footer — removed~~ (`ea35edf`)
- ~~Nudge button on active agent cards — removed~~ (`ea35edf`)
- ~~Pet Maiko widget → footer chip~~ (`7ed979f`)
- ~~Agent profile cards redesigned~~ (collapsible roles + mini cards + modal) (`c6393f7`)

*Add more as you notice them; resolve them in whatever order feels good.*
