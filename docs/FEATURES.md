## Full list of features
Last Updated: 05/14/26

### The pack
- Agent orchestration. Maiko kicks off agents, manages their lifecycles, mediates conflicts.
- Worktree-isolated runs. Each agent works in its own git worktree, so siblings don't step on each other.
- A2A conflict detection. Catches file and API overlap between sibling agents before damage is done.
- In-app diff review. Read the agent's PR diff, leave comments, request changes or approve, without leaving Maiko.
- Per-agent personalities pulled from the deck. Agents have names, archetypes, and opinions.

### Memory and learning
- RAG retrieval over your team's accumulated conventions. Agents fetch only what's relevant.
- Learnings extracted from your PR review history. Reviewer feedback becomes durable rules without prompt engineering.
- Approved insights inject into every new agent automatically.
- Pack Insights ritual at end of day. Agents share what they learned at the campfire, you approve what sticks, tomorrow's pack wakes smarter.
- Rules export and import. Share your learned rules with a teammate so they don't have to start from scratch.

### The world
- Daily home overview, generated for you.
- Curated themes. Pick the register that fits your day.
- Live weather and sprite moods that shift with your local time.
- Your pack is always within arms reach, persisted on each page.

### The plumbing
- Unified AgentJob execution model. Every agent run (manual, automated, skill-driven) goes through one path.
- Automations. iPhone-style "when X happens, do Y" rules. No LLM in the trigger layer, just predicates.
- Custom skills, plugin-defined.
- Plugin architecture. Drop a `.py` file in `~/.maiko/plugins/` to wire up anything.
- Repo checks (`check_code`). Mechanical verdict before an agent says it's done.
- Model + runtime routing. Pick which model and which runtime (headless Claude, interactive Claude in tmux, or a local Ollama-served model) handles which kind of work.

### Built-in integrations
- GitHub
- Linear
- Calendar
- PagerDuty