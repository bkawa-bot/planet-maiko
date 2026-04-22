"""Repo-level code pattern analysis for training-data generation.

Rule-gen produces better synthetic code when it knows HOW code is
written in the target repo — naming, idioms, testing style, internal
libraries. This module runs a one-shot read-only agent per repo and
caches the result so we pay the LLM cost once per repo per TTL window,
not once per rule-gen run.

Output is ~1500 words of markdown stored at
`data/repo-patterns/<safe_repo>.md`. A sibling `.meta.json` tracks the
generation timestamp for TTL invalidation.

Distinct from the cartographer: cartographer writes a high-level repo
overview (architecture, gotchas, vibe) into every agent's CLAUDE.md.
Repo patterns are code-level details only used by the training-data
generator — naming conventions, internal lib paths, test framework,
idioms — the stuff that shapes what a realistic code snippet LOOKS
like.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 30

REPO_PATTERNS_PROMPT = """You are generating code-level patterns for a training-data generator.

Explore this repo and produce a concise markdown document describing HOW
people write code here — the conventions, idioms, internal libraries,
and naming patterns that make realistic code for this repo look the way
it does.

A separate tool uses your output as context for generating synthetic
code examples that should look like they came from this repo. Focus on
patterns that affect what a realistic snippet LOOKS like: naming,
imports, testing idioms, error handling, common library calls.

## What to include

- **Primary language(s) and framework(s)** (e.g., Python + FastAPI, TypeScript + React)
- **Test framework** and typical test patterns (pytest fixtures? jest + react-testing-library? table-driven?)
- **Internal libraries** you see imported in source files — give the actual import paths (e.g., `from foo.shared.logging import log`)
- **Naming conventions** (snake_case functions, camelCase React components, PascalCase types, etc.)
- **Error handling style** (exceptions? Result types? try/except with logging?)
- **Common imports and idioms** that recur across files
- **Don't-do list** — stuff the team clearly does NOT do (e.g., "no raw SQL — always via SQLAlchemy")

## What to skip

- Business logic, product features, or domain concepts — focus on style
- High-level architecture — a separate cartographer already covers that
- Security audits or code review opinions

## How to explore

Use your Read, Glob, Grep, and Bash tools:
- Check package manifests (package.json, pyproject.toml, go.mod, Cargo.toml) for the stack
- Read the README if present
- Glance at 5-10 representative source files across the main source directories
- Look at 2-3 tests to see the testing style
- Note recurring imports (e.g., `grep -rh "^import\\|^from" src/ | sort | uniq -c | sort -rn | head -30`)

## Output

Respond with markdown only. Start directly with `# Repo Patterns`. No
preamble, no code fencing around the whole output. Max ~1500 words.
Use headings so the downstream generator can reference sections.
"""


def _patterns_dir():
    from planet_maiko.paths import data_dir
    return os.path.join(data_dir(), "repo-patterns")


def _safe_repo_name(repo):
    return repo.replace("/", "--").replace("\\", "--")


def _pattern_path(repo):
    return os.path.join(_patterns_dir(), f"{_safe_repo_name(repo)}.md")


def _meta_path(repo):
    return os.path.join(_patterns_dir(), f"{_safe_repo_name(repo)}.meta.json")


def _is_fresh(repo, ttl_days):
    meta_path = _meta_path(repo)
    if not os.path.exists(meta_path):
        return False
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        generated_at = datetime.fromisoformat(meta["generated_at"])
        age = datetime.now(timezone.utc) - generated_at
        return age < timedelta(days=ttl_days)
    except Exception:
        return False


def _read_cached(repo):
    path = _pattern_path(repo)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _write(repo, content):
    os.makedirs(_patterns_dir(), exist_ok=True)
    with open(_pattern_path(repo), "w", encoding="utf-8") as f:
        f.write(content)
    with open(_meta_path(repo), "w", encoding="utf-8") as f:
        json.dump({
            "repo": repo,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }, f)


def get_repo_patterns(repo, force=False, ttl_days=DEFAULT_TTL_DAYS):
    """Return markdown describing the repo's code patterns.

    Checks the cache at data/repo-patterns/<safe_repo>.md. If fresh
    (within ttl_days) and not forced, returns the cached content.
    Otherwise invokes a read-only agent to generate a new analysis.

    Returns None if:
      - repo is empty
      - repo has no local checkout (resolve_repo_path returns None)
      - the LLM runtime is unavailable
      - the LLM call failed or returned empty
    """
    if not repo:
        return None

    if not force and _is_fresh(repo, ttl_days):
        cached = _read_cached(repo)
        if cached:
            logger.info(f"[repo-patterns] Cache hit for {repo}")
            return cached

    from planet_maiko.orchestration import resolve_repo_path
    repo_path = resolve_repo_path(repo)
    if not repo_path:
        logger.info(
            f"[repo-patterns] No local checkout for {repo}; skipping analysis"
        )
        return None

    from planet_maiko.agents.brain_session import _get_runtime
    runtime = _get_runtime()
    if not runtime or not runtime.is_available():
        logger.warning("[repo-patterns] LLM runtime unavailable")
        return None

    logger.info(
        f"[repo-patterns] Generating fresh patterns for {repo} ({repo_path})"
    )
    result = runtime.send(
        REPO_PATTERNS_PROMPT,
        working_dir=repo_path,
        timeout=600,  # exploration + synthesis
        allowed_tools=["Read", "Glob", "Grep", "Bash"],
        permission_mode="plan",
    )

    if not result.get("success"):
        logger.warning(
            f"[repo-patterns] Analysis failed for {repo}: {result.get('error')}"
        )
        return None

    output = (result.get("output") or "").strip()
    if not output:
        logger.warning(f"[repo-patterns] Empty output for {repo}")
        return None

    _write(repo, output)
    logger.info(f"[repo-patterns] Saved {len(output)} chars for {repo}")
    return output
