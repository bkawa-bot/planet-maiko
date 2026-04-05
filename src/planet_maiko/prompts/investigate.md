# Maiko Investigate

Takes a suggestion, error, or vague concern and turns it into a scoped, prioritized, actionable item — with the right person/agent identified to tackle it.

## Topic
{query}

## Context
{context}

## Available Data
- Recent pupdates: {pupdates}
- Active tasks: {tasks}
- Calendar: {calendar}

## Instructions

### Step 1: Scope the Issue

Based on the topic and context, gather what you can:

**For errors / service issues:**
- Look for error patterns, affected endpoints, volume trends
- Check if there were recent deploys or changes

**For stuck/stale PRs:**
- What's blocking it? Missing review? CI failure? Merge conflicts?
- How long has it been open?

**For suggestions:**
- Is this already being tracked as a task or project?
- Is this a symptom of a bigger issue?

**For any investigation:**
- Is there an existing task or project covering this?
- What repo(s) are affected?

### Step 2: Assess Priority

Score on two axes:

**Impact** (how bad is it?):
- `critical`: Production errors affecting users, SLA violations, blocked deploys
- `high`: Increasing error trends, PRs blocked, flaky tests in critical paths
- `medium`: Stale PRs, backlog items aging, non-critical gaps
- `low`: Cleanup, nice-to-haves, cosmetic issues

**Effort** (how hard to fix?):
- `small`: < 1 hour, single file change, config fix
- `medium`: Half day, single PR, well-scoped
- `large`: Multi-day, multi-PR, needs design
- `xl`: Multi-week, cross-team, needs buy-in

### Step 3: Identify Who/What Should Handle This

Consider:
- Which agent context strategy (if any) has experience with this repo + category?
- If no agent fits, recommend creating a new one (exploration pup)
- Is this something the user should handle themselves?

### Step 4: Present the Report

Use this exact format:

```
# Investigation: {title}

## Summary
{1-2 sentence description}

## Findings
{What you found — data, patterns, root cause hypothesis}

## Impact & Priority
- **Impact**: {critical/high/medium/low} — {why}
- **Effort**: {small/medium/large/xl} — {why}
- **Recommended priority**: {P0/P1/P2/P3}

## Who Should Handle This
- **Best fit**: {agent name or "user"} — {why}
- **Repo**: {affected repo(s)}
- **Categories**: {testing, security, api_design, etc.}

## Suggested Next Steps
{Concrete actions — not generic "investigate further"}

## Actions Available
1. **Dismiss** — not worth pursuing
2. **Create task** — add to the task list with this priority
3. **Create project** — set up a project with phases
4. **Assign agent** — prepare an agent to start working on it
```

Be thorough but concise. Write the summary like you'd explain it to the user over coffee — conversational, clear, no jargon.
