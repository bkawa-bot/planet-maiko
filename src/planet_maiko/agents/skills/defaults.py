"""Default skill definitions seeded on first run.

Users can edit these from the Skills page. The prompts use
{variable} placeholders for context injection and can reference
MCPs the user has configured.
"""

DEFAULT_SKILLS = [
    {
        "id": "investigate",
        "name": "Investigate",
        "description": "Deep dive into a specific issue or topic",
        "icon": "search",
        "mcps": ["slack", "linear"],
        "needs_worktree": True,
        "prompt": """# Investigation

You are Maiko, a senior engineer investigating an issue. Do a deep dive on the following topic.

## Topic
{query}

## Context
{context}

## Instructions
1. Analyze the available information
2. Look for root causes, patterns, and correlations
3. Check relevant code, logs, and metrics if available (use MCPs if configured)
4. Provide a structured investigation report with:
   - Summary of findings
   - Root cause (confirmed or hypothesized)
   - Impact assessment
   - Recommended actions
   - What to monitor going forward

Be thorough but concise. Use markdown formatting.""",
    },
    {
        "id": "repo-analysis",
        "name": "Repo Analysis",
        "description": "Analyze a repository for code health and improvement opportunities",
        "icon": "git-fork",
        "mcps": [],
        "needs_worktree": True,
        "prompt": """# Repository Analysis

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
    },
    {
        "id": "plan",
        "name": "Smart Planner",
        "description": "Optimize your work order — groups by repo, respects calendar, minimizes context switching",
        "icon": "calendar",
        "mcps": [],
        "prompt": "Create an optimized work schedule from tasks, calendar, and pupdates.",
    },
    {
        "id": "team",
        "name": "Team Snapshot",
        "description": "See what everyone's working on and where things are stuck",
        "icon": "users",
        "mcps": [],
        "prompt": "Summarize team activity, review bottlenecks, and agent status.",
    },
    {
        "id": "pr-review",
        "name": "PR Review",
        "description": "Review a pull request for bugs, design, testing, and code quality",
        "icon": "eye",
        "mcps": ["github"],
        # PR review needs a worktree: the agent has to fetch the diff,
        # read code in context, and post inline comments via MCP. The
        # lightweight (no-worktree) skill path is a single LLM call
        # with no Read/Bash/leave_comment — no way to actually review.
        "needs_worktree": True,
        "prompt": "Review a pull request.",
    },
    {
        "id": "agent-protocol",
        "name": "Agent Instructions",
        "description": "Additional instructions injected into every coding agent's CLAUDE.md. Edit this to customize what agents receive.",
        "icon": "bot",
        "mcps": [],
        "prompt": "Agent protocol template — edit this from the Skills page to customize agent behavior.",
    },
]
