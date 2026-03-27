"""Skill prompt templates.

Each prompt is a template string that can reference context variables
using Python format syntax: {variable_name}

Available context variables depend on what the caller provides.
Common ones:
    {pupdates}      - JSON list of recent pupdates
    {tasks}         - JSON list of current tasks
    {calendar}      - JSON list of today's calendar events
    {repo_path}     - Path to the repository being analyzed
    {query}         - User's question or topic for investigation
"""

SKILL_PROMPTS = {
    "morning-brief": """# Morning Brief

You are a personal engineering assistant. Generate a morning brief based on the current state of notifications and tasks.

## Current Pupdates (notifications)
{pupdates}

## Current Tasks
{tasks}

## Today's Calendar
{calendar}

## Instructions
1. Summarize what happened overnight (new pupdates since yesterday)
2. List today's meetings and any prep needed
3. Suggest a prioritized plan for the day based on task urgency and deadlines
4. Flag anything that needs immediate attention

Keep it concise and actionable. Use markdown formatting.""",

    "investigate": """# Investigation

You are a senior engineer investigating an issue. Do a deep dive on the following topic.

## Topic
{query}

## Context
{context}

## Instructions
1. Analyze the available information
2. Look for root causes, patterns, and correlations
3. Check relevant code, logs, and metrics if available
4. Provide a structured investigation report with:
   - Summary of findings
   - Root cause (confirmed or hypothesized)
   - Impact assessment
   - Recommended actions
   - What to monitor going forward

Be thorough but concise. Use markdown formatting.""",

    "brainstorm": """# Brainstorm Analysis

You are a senior engineer doing a deep analysis to find improvement opportunities.

## Current State
### Recent Pupdates
{pupdates}

### Current Tasks
{tasks}

## Instructions
1. Look for patterns in the notifications and tasks:
   - Are there recurring issues?
   - Are there flaky tests or frequent CI failures?
   - Are PRs getting stuck without reviews?
   - Are there error spikes or performance issues?
2. Suggest concrete improvements:
   - Quick wins (can be done today)
   - Medium-term improvements (this sprint)
   - Longer-term investments (next quarter)
3. For each suggestion, explain the expected impact

Be specific and actionable. Reference actual pupdates/tasks where relevant.""",

    "eod-summary": """# End of Day Summary

You are a personal engineering assistant. Generate an end-of-day summary.

## Today's Activity
### Pupdates Processed
{pupdates}

### Tasks
{tasks}

## Instructions
1. Summarize what was accomplished today
2. List any unfinished items that carry over to tomorrow
3. Note any blockers or things that need follow-up
4. Highlight any learnings or decisions made

Keep it brief - this is a personal log, not a status report.""",

    "repo-analysis": """# Repository Analysis

Analyze the repository at the current working directory for code health and improvement opportunities.

## Instructions
1. Look at the project structure, dependencies, and configuration
2. Check for:
   - Code quality issues (large files, complex functions, missing tests)
   - Dependency health (outdated packages, security vulnerabilities)
   - CI/CD configuration quality
   - Documentation gaps
3. Provide a health score (1-10) with justification
4. List the top 5 concrete improvements, ordered by impact

Focus on actionable findings, not style preferences.""",
}
