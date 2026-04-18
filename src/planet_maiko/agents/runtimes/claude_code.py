"""Claude Code runtime - runs prompts through the claude CLI.

This is the default runtime for Planet Maiko. It spawns claude CLI
processes to handle agent work, leveraging Claude Code's built-in
tool use, MCP integrations, and file system access.

For the brain session, we use --print mode for quick prompt/response.
For coding agents, we use full interactive sessions in git worktrees.
"""

import json
import logging
import os as _os
import shutil
import subprocess

from planet_maiko.agents.runtimes.base import AgentRuntime

logger = logging.getLogger(__name__)


class ClaudeCodeRuntime(AgentRuntime):

    @property
    def name(self):
        return "claude-code"

    def _find_claude(self):
        """Find the claude CLI, checking common install locations."""
        found = shutil.which("claude")
        if found:
            return found
        # Check common locations not always in PATH
        import os
        for path in [
            os.path.expanduser("~/.local/bin/claude"),
            "/usr/local/bin/claude",
            os.path.expanduser("~/.claude/bin/claude"),
        ]:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    def is_available(self):
        return self._find_claude() is not None

    def _get_allowed_tools(self):
        """Load allowed tools: config.brain.allowed_tools plus every MCP
        server that's globally registered with Claude Code.

        Users who've already configured Linear / Slack / Sentry / etc.
        in their Claude Code settings shouldn't have to re-list them in
        brain.allowed_tools — one-shot skill calls just get everything
        their interactive Claude Code sessions already have.
        """
        try:
            from planet_maiko.config import load_config
            base = list(load_config().get("brain", {}).get("allowed_tools", []))
        except Exception:
            base = []
        for mcp_tool in self._discover_global_mcps():
            if mcp_tool not in base:
                base.append(mcp_tool)
        return base

    def _discover_global_mcps(self):
        """Return [mcp__<server>, ...] for every MCP registered with
        Claude Code — both globally and across all known projects.
        Best-effort: returns [] if no settings file is readable.

        Claude Code stores MCPs in two places in ~/.claude.json:
            - top-level `mcpServers` (globally available)
            - `projects[<path>].mcpServers` (project-specific)
        We union both so a user who configured Linear once in a
        project doesn't have to re-add it globally for skill runs.
        """
        import os
        import json as _json
        names = set()
        candidates = [
            os.path.expanduser("~/.claude.json"),
            os.path.expanduser("~/.claude/settings.json"),
            os.path.expanduser("~/.config/claude/settings.json"),
        ]
        for path in candidates:
            try:
                if not os.path.isfile(path):
                    continue
                with open(path, encoding="utf-8") as f:
                    data = _json.load(f)
                global_servers = data.get("mcpServers") or {}
                if isinstance(global_servers, dict):
                    names.update(global_servers.keys())
                projects = data.get("projects") or {}
                if isinstance(projects, dict):
                    for proj in projects.values():
                        if not isinstance(proj, dict):
                            continue
                        proj_servers = proj.get("mcpServers") or {}
                        if isinstance(proj_servers, dict):
                            names.update(proj_servers.keys())
            except Exception:
                continue
        return [f"mcp__{name}" for name in sorted(names)]

    def _get_thinking_budget(self):
        """Load thinking budget from config."""
        try:
            from planet_maiko.config import load_config
            return load_config().get("routing", {}).get("thinking_budget", "medium")
        except Exception:
            return "medium"

    def _build_subprocess_env(self):
        """Env for the claude subprocess. Inherits the parent's env and
        adds ENABLE_PROMPT_CACHING_1H=1 when brain.prompt_cache_1h is on
        — Anthropic's Apr 14 2026 change lets you opt long-running
        sessions into a 1-hour cache TTL instead of the default 5m,
        which usually wins on cost for multi-turn agent sessions.

        Returns None when there's nothing to override (subprocess.run
        treats None as "inherit"), so the happy path does not construct
        a dict per call.
        """
        try:
            from planet_maiko.config import load_config
            brain = load_config().get("brain", {})
        except Exception:
            return None
        if not brain.get("prompt_cache_1h"):
            return None
        env = dict(_os.environ)
        env["ENABLE_PROMPT_CACHING_1H"] = "1"
        return env

    def send(self, prompt, working_dir=None, timeout=300, model=None, allowed_tools=None, session_id=None, skip_permissions=False, permission_mode=None):
        """Send a prompt to claude CLI in print mode.

        Uses --print for single prompt/response (no interactive session).
        The prompt is piped via stdin rather than passed as a command-
        line argument — our prompts (agent preamble + role protocol +
        skill content + task context) can exceed Windows' ~32K argv
        limit and get silently truncated, producing the "input must
        be provided either through stdin or as a prompt argument"
        error from claude. stdin has no such limit.

        If session_id is provided, the run is saved under that id so
        later "View Session" / resume lookups can find the transcript
        at ~/.claude/projects/<escaped-path>/<session_id>.jsonl.

        skip_permissions=True adds --dangerously-skip-permissions for
        autonomous runs where there's no human to answer tool-approval
        prompts. Only safe inside isolated worktrees we own — the
        caller is responsible for the sandbox.

        permission_mode (e.g. "plan") maps to Claude Code's
        --permission-mode flag. "plan" restricts to read-only tools
        (Read/Glob/Grep/Bash without writes) — perfect for analysis
        runs that should explore the repo without being able to
        modify it. Mutually useful with skip_permissions=False so the
        sandbox is enforced by the runtime, not by trust.
        """
        if not prompt or not prompt.strip():
            return {
                "output": "",
                "success": False,
                "error": "Empty prompt — nothing to send",
            }

        claude_path = self._find_claude() or "claude"
        cmd = [claude_path, "--print", "--output-format", "text"]

        if session_id:
            cmd.extend(["--session-id", session_id])

        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        if permission_mode:
            cmd.extend(["--permission-mode", permission_mode])

        # Model override for cost-aware routing
        if model:
            cmd.extend(["--model", model])

        # Effort level (controls Claude's reasoning depth)
        budget = self._get_thinking_budget()
        if budget in ("low", "medium", "high", "max"):
            cmd.extend(["--effort", budget])

        # Pre-approve tools to avoid permission prompts. Per-call
        # allowed_tools overrides the config default; this is how
        # per-skill MCP wiring grants specific MCPs for a given run.
        #
        # When skip_permissions is on, --allowedTools actively hurts:
        # the skip flag is a blanket "don't prompt for anything", but
        # --allowedTools is treated as a restrictive scope filter.
        # Writing to "REVIEW.md" without a matching Write(**) glob, or
        # calling mcp__maiko-channel__reply without naming the
        # specific sub-tool, would still stall. The skip flag alone
        # is the right behavior for headless / autonomous runs.
        if not skip_permissions:
            if allowed_tools is None:
                allowed_tools = self._get_allowed_tools()
            for tool in allowed_tools:
                cmd.extend(["--allowedTools", tool])

        try:
            result = subprocess.run(
                cmd,
                input=prompt,  # pipe via stdin, not argv — avoids length caps
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=working_dir,
                env=self._build_subprocess_env(),
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or f"claude exited with code {result.returncode}"
                logger.error(f"[claude-code] Error: {error_msg}")
                return {
                    "output": result.stdout.strip(),
                    "success": False,
                    "error": error_msg,
                }

            return {
                "output": result.stdout.strip(),
                "success": True,
                "error": None,
            }

        except subprocess.TimeoutExpired:
            logger.error(f"[claude-code] Timed out after {timeout}s")
            return {
                "output": "",
                "success": False,
                "error": f"Timed out after {timeout}s",
            }
        except FileNotFoundError:
            return {
                "output": "",
                "success": False,
                "error": "claude CLI not found. Install Claude Code first.",
            }

    def send_json(self, prompt, working_dir=None, timeout=300, model=None, allowed_tools=None, permission_mode=None):
        """Send a prompt and parse the response as JSON.

        Wraps the prompt with instructions to return JSON.
        Used for structured decisions (triage, task creation, etc.).
        """
        json_prompt = (
            f"{prompt}\n\n"
            "Respond with ONLY valid JSON, no markdown fencing, no explanation."
        )

        result = self.send(json_prompt, working_dir=working_dir, timeout=timeout, model=model, allowed_tools=allowed_tools, permission_mode=permission_mode)

        if not result["success"]:
            return result

        # Try to parse JSON from the output
        output = result["output"].strip()
        # Strip markdown fencing if present
        if output.startswith("```"):
            lines = output.split("\n")
            output = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        try:
            parsed = json.loads(output)
            result["parsed"] = parsed
        except json.JSONDecodeError as e:
            logger.warning(f"[claude-code] Failed to parse JSON: {e}")
            result["parsed"] = None
            result["error"] = f"Invalid JSON response: {e}"

        return result

    def get_info(self):
        info = super().get_info()
        if info["available"]:
            try:
                result = subprocess.run(
                    [self._find_claude() or "claude", "--version"],
                    capture_output=True, text=True, timeout=5,
                )
                info["version"] = result.stdout.strip()
            except Exception:
                info["version"] = "unknown"
        return info
