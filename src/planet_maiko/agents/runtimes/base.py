"""Agent runtime base class — the contract every backend implements.

Maiko was originally scaffolded as runtime-pluggable (the
`runtimes/` package, the `_get_runtime()` indirection in
brain_session.py), but only `ClaudeCodeRuntime` ever shipped. With
Anthropic splitting agentic from interactive usage starting June 15,
2026, the project needs to be ready to swap or supplement claude-code
with another backend (Aider, Codex CLI, Goose, a local-Ollama agent
loop, or interactive Claude in a PTY to dodge the credit pool).

This module defines `AgentRuntime`, the abstract class every
implementation subclasses. The contract has two sides:

  1. **Synchronous one-shot** (send / send_json) — used by skills,
     chat, brain triage, and short model judgments. Block the caller
     until the prompt returns a single response.

  2. **Asynchronous spawn** (spawn) — used to launch a long-running
     coding / review / investigation / cartographer agent in a git
     worktree. Returns immediately; the agent runs in the background
     and talks back through MCP (today) or whatever channel the
     runtime exposes. Today this path lives in
     agents/runtime/kickoff.py with claude wired in directly; the
     intention is to move that logic into runtime.spawn() so the
     kickoff phase is runtime-agnostic too. See AGENT_RUNTIME.md for
     migration notes.

A new runtime only HAS to implement the synchronous methods. The
async spawn() is opt-in — sync-only backends raise NotImplementedError
and Maiko falls back to ClaudeCodeRuntime for agent kickoff while the
new backend handles skill / chat / triage runs.

Beyond this class, AGENT_RUNTIME.md captures the *implicit* coupling
that lives outside the runtime layer (MCP, the Stop hook, the
CLAUDE.md prompt format, etc.) — those are the parts a real
non-claude backend would have to substitute or stub.
"""

from abc import ABC, abstractmethod
from typing import Any, Optional


class AgentRuntime(ABC):
    """The contract every agent runtime fulfills.

    Subclasses set `name` to a short stable id ("claude-code", "aider",
    "codex", etc.) and implement at least the synchronous methods.
    `spawn()` is optional but recommended if the runtime can drive a
    multi-tool agent in a working directory.
    """

    #: Short stable identifier. Used in logs, the runtime registry,
    #: and the Settings → Model Routing UI.
    name: str = "abstract"

    # ----- Availability + introspection -----

    @abstractmethod
    def is_available(self) -> bool:
        """True when the underlying binary / API / service can be reached.

        Cheap to call; should not perform a real LLM round-trip. The
        result is shown in Settings so the user knows the runtime
        won't error at the moment it's invoked.
        """

    def get_info(self) -> dict[str, Any]:
        """Return basic metadata about this runtime.

        Default implementation returns {name, available}. Subclasses
        usually override to add a `version` field.
        """
        return {"name": self.name, "available": self.is_available()}

    # ----- Synchronous one-shot prompt -> response -----
    # These are the methods skills, chat, and brain triage call. The
    # caller blocks until the runtime returns. Result shape:
    #     {"output": str, "success": bool, "error": str|None}
    # send_json adds {"parsed": dict|None} when the output was valid JSON.

    @abstractmethod
    def send(
        self,
        prompt: str,
        working_dir: Optional[str] = None,
        timeout: int = 300,
        model: Optional[str] = None,
        allowed_tools: Optional[list[str]] = None,
        session_id: Optional[str] = None,
        skip_permissions: bool = False,
        permission_mode: Optional[str] = None,
        effort: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run `prompt` synchronously and return one response.

        Args mirror the union of what existing callers pass. Runtimes
        that don't understand a flag (e.g. Aider doesn't have
        `effort`) should ignore it silently rather than raise — the
        caller may be looping over multiple runtimes.

        Return shape:
            {
                "output": str,    # the agent's text response
                "success": bool,
                "error": str | None,
            }
        """

    @abstractmethod
    def send_json(
        self,
        prompt: str,
        working_dir: Optional[str] = None,
        timeout: int = 300,
        model: Optional[str] = None,
        allowed_tools: Optional[list[str]] = None,
        permission_mode: Optional[str] = None,
        effort: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run `prompt` and parse the response as JSON.

        Same shape as send() plus `parsed` (dict or None) and an
        `error` populated when the response wasn't valid JSON.
        """

    # ----- Asynchronous agent spawn — optional -----
    # Used by the brain cycle's execute_jobs phase to launch a
    # long-running agent in an isolated working directory. The agent
    # is expected to call back via MCP / outbox / log file (whatever
    # this runtime's protocol is — see AGENT_RUNTIME.md).
    #
    # Today the only implementation that supports this is
    # ClaudeCodeRuntime, and the actual spawning still lives in
    # agents/runtime/kickoff.py rather than the class. The contract
    # is documented here so a new runtime knows what to fulfill when
    # we finish migrating kickoff into runtime.spawn().

    def spawn(
        self,
        working_dir: str,
        initial_prompt: str,
        session_id: str,
        *,
        job_id: Optional[str] = None,
        mcp_config_path: Optional[str] = None,
        log_path: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        permission_mode: Optional[str] = None,
        extra_env: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Spawn an autonomous agent in `working_dir`. Optional.

        Encapsulates the "run the agent and wait for it to exit" loop.
        Designed to be called from a daemon thread — the call BLOCKS
        until the underlying subprocess settles. The user-facing
        async behavior (return-immediately, work in background) is
        provided by callers like ``agents/runtime/kickoff.py`` that
        wrap spawn() in a thread.

        Args:
            working_dir: where the agent runs (its CWD)
            initial_prompt: prompt fed to the agent on startup
            session_id: persisted as the runtime's session id for resume
            job_id: optional; if provided, the runtime registers the
                subprocess with ``agents/runtime/process.py`` so
                cancellation (``stop_agent_session``) can reach into
                the running process and terminate it
            mcp_config_path: passed to claude as ``--mcp-config`` when
                non-None and the file exists; ignored by runtimes
                that don't speak MCP
            log_path: stdout + stderr teed here; if None, output is
                captured in memory (less useful for long agent runs)
            model: maps to the runtime's model flag (claude:
                ``--model``)
            effort: maps to ``--effort low/medium/high/max`` on
                claude-code; runtimes that have no equivalent should
                silently ignore
            permission_mode: maps to ``--permission-mode`` on
                claude-code (e.g. "plan" for read-only); runtimes
                without an equivalent should silently ignore
            extra_env: additional env vars merged into ``os.environ``
                for the subprocess

        Return shape:
            {
                "success": bool,
                "pid": int | None,
                "exit_code": int | None,
                "error": str | None,
                "log_tail": str | None,   # last ~400 chars of the log
                                          # on failure, helps surface
                                          # the why-it-crashed without
                                          # needing log file access
            }

        Sync-only runtimes that don't drive multi-tool agents in a
        worktree should leave this raise (the default) so the caller
        falls back to a different runtime for kickoff.
        """
        raise NotImplementedError(
            f"{self.name} runtime does not support spawn(); use a runtime "
            f"that drives autonomous agent sessions (e.g. claude-code) for "
            f"this work."
        )

    def supports_spawn(self) -> bool:
        """True when this runtime can launch an autonomous agent.

        Default: introspects whether `spawn` was overridden. Override
        in a subclass to short-circuit (e.g. when spawn exists but
        depends on an optional dependency).
        """
        return type(self).spawn is not AgentRuntime.spawn
