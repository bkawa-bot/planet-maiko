# Backlog

Brain-dump of small fixes and design questions. Nothing here blocks anything shipping today. Pick one at a time when you have energy.

## Bugs to confirm

- **Shutdown flow might not actually stop the service.** Blank progress bar says *"Maiko is shutting down"* but the process seems to keep running. Check `/api/system/shutdown` SIGTERM path and whether the frontend correctly detects completion.

## Design questions (not quick fixes)

- **Do investigation agents auto-run on high-priority poller alerts?** If the GitHub poller fires an error-spike or stale-deploy signal, what happens today? Gut: it becomes a pupdate, maybe surfaces in Pack Requests, but no auto-investigation — which means you could miss real fires while away. Worth designing: which alert types trigger an auto-investigation, what the agent does, how the user approves or dismisses.
- **What happens when an investigation agent finds something worth fixing?** Today it surfaces a report as a pupdate and the user decides. Could be smoother — e.g. `PROPOSAL:` blocks in the investigation output auto-spawn a queued coding task the user can approve or dismiss. Related: `ProposalCard.jsx` exists but isn't mounted anywhere; it was built for the "From Maiko" approval queue which then got orphaned. Any fix here probably revives that surface.
- **Skills feel disconnected from the pack.** Skills (morning brief, brainstorm, repo-analysis) are prompt templates running on a schedule, outside the agent system. Possible reframe: skills are agent-task templates, not their own category. Scheduled skills = scheduled agent tasks. A repo-analysis skill would be a cartographer agent's task, not a standalone skill run.

---

## Done (2026-04-19)

- ~~Afternoon theme — gutted~~ (`ad538d3`)
- ~~Frosted panes more transparent~~ (`ad538d3`)
- ~~Weekend pill — gated to Sat/Sun or when on~~ (`ea35edf`)
- ~~Duplicate "available" signal in footer — removed~~ (`ea35edf`)
- ~~Nudge button on active agent cards — removed~~ (`ea35edf`)
- ~~Pet Maiko widget → footer chip~~ (`7ed979f`)
- ~~Agent profile cards redesigned~~ (collapsible roles + mini cards + modal) (`c6393f7`)
- ~~"Ask Maiko" → "Ask the pack" inline on Home~~ (removed floating bubble) (`02d2a25`)
- ~~Training folded into Knowledge as a 4th tab~~ (`ea63aef`)
- ~~"Open" button on overview "needs" — hidden when it would bounce back to Home~~ (`93267ea`)

*Add more as you notice them; resolve them in whatever order feels good.*
