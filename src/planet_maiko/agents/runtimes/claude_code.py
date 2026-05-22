"""Claude Code runtime - runs prompts through the claude CLI.

This is the default runtime for Planet Maiko. It spawns claude CLI
processes to handle agent work, leveraging Claude Code's built-in
tool use, MCP integrations, and file system access.

For the brain session, we use --print mode for quick prompt/response.
For coding agents, we use full interactive sessions in git worktrees.

Implements the AgentRuntime ABC in `base.py`. The async spawn path
lives in agents/runtime/kickoff.py; see AGENT_RUNTIME.md.
"""

import json
import logging
import os as _os
import shutil
import subprocess

from .base import AgentRuntime

logger = logging.getLogger(__name__)


class ClaudeCodeRuntime(AgentRuntime):

    name = "claude-code"

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

    def send(self, prompt, working_dir=None, timeout=300, model=None, allowed_tools=None, session_id=None, skip_permissions=False, permission_mode=None, effort=None, source=None):
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
        # JSON output gets us the usage block (input/output/cache tokens,
        # cost estimate, duration) so every internal Maiko call lands a
        # TokenUsage row. The text the model produced still surfaces as
        # `output`; callers don't need to change.
        cmd = [claude_path, "--print", "--output-format", "json"]

        if session_id:
            cmd.extend(["--session-id", session_id])

        if skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        if permission_mode:
            cmd.extend(["--permission-mode", permission_mode])

        # Model override for cost-aware routing
        if model:
            cmd.extend(["--model", model])

        # Effort level (controls Claude's reasoning depth). Per-call
        # effort wins over the global thinking_budget default — that's
        # how cheap classification calls (triage, scene) avoid paying
        # for deep reasoning while coding agents still get it.
        budget = effort or self._get_thinking_budget()
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

            # JSON output: parse the wrapper to get `result` (the model's
            # text) plus the usage block. If parsing fails (older claude
            # CLI that doesn't support --output-format json, or a
            # malformed line), fall back to treating stdout as text.
            stdout = result.stdout.strip()
            output_text = stdout
            usage = None
            session_used = None
            duration_ms = None
            total_cost_usd = None
            try:
                import json as _json
                data = _json.loads(stdout)
                if isinstance(data, dict):
                    output_text = (data.get("result") or "").strip() or stdout
                    usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
                    session_used = data.get("session_id") or session_id
                    duration_ms = data.get("duration_ms")
                    total_cost_usd = data.get("total_cost_usd")
            except Exception:
                pass

            self._log_usage(
                source=source,
                model=model,
                usage=usage,
                duration_ms=duration_ms,
                total_cost_usd=total_cost_usd,
                session_id=session_used,
            )

            return {
                "output": output_text,
                "success": True,
                "error": None,
                "usage": usage,
                "duration_ms": duration_ms,
                "total_cost_usd": total_cost_usd,
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

    def _log_usage(self, source, model, usage, duration_ms, total_cost_usd, session_id):
        """Best-effort write of a TokenUsage row for this LLM call.

        Swallows everything: failure to log shouldn't bubble up and break
        an actual LLM result, and the logger should also no-op cleanly
        when there's no Flask app context (CLI tools, one-shot scripts).
        """
        if not usage:
            return
        try:
            from planet_maiko.database import db
            from planet_maiko.models.token_usage import TokenUsage
            row = TokenUsage(
                source=(source or "unknown")[:100],
                model=(model or "")[:100] or None,
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_creation_tokens=int(usage.get("cache_creation_input_tokens") or 0),
                cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
                total_cost_usd=float(total_cost_usd) if total_cost_usd is not None else None,
                duration_ms=int(duration_ms) if duration_ms is not None else None,
                session_id=session_id,
            )
            db.session.add(row)
            db.session.commit()
        except Exception as e:
            logger.debug(f"[claude-code] token usage log failed: {e}")

    def send_json(self, prompt, working_dir=None, timeout=300, model=None, allowed_tools=None, permission_mode=None, effort=None, source=None):
        """Send a prompt and parse the response as JSON.

        Wraps the prompt with instructions to return JSON.
        Used for structured decisions (triage, task creation, etc.).
        """
        json_prompt = (
            f"{prompt}\n\n"
            "Respond with ONLY valid JSON, no markdown fencing, no explanation."
        )

        result = self.send(json_prompt, working_dir=working_dir, timeout=timeout, model=model, allowed_tools=allowed_tools, permission_mode=permission_mode, effort=effort, source=source)

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

    def spawn(
        self,
        working_dir,
        initial_prompt,
        session_id,
        *,
        job_id=None,
        mcp_config_path=None,
        log_path=None,
        model=None,
        effort=None,
        permission_mode=None,
        extra_env=None,
    ):
        """Run a `claude --print` agent process synchronously and wait
        for it to exit. See AgentRuntime.spawn for the full contract.

        Any caller (the existing kickoff daemon thread, a future
        runtime-picker UI, a test harness) can launch a claude agent
        through this without rebuilding the flag set.
        """
        if not prompt_has_text(initial_prompt):
            return _spawn_error("Empty initial prompt — nothing to send")

        claude_path = self._find_claude()
        if not claude_path:
            return _spawn_error("claude CLI not found")

        cmd = [
            claude_path, "--print", "--output-format", "text",
            "--session-id", session_id,
            "--dangerously-skip-permissions",
        ]

        # Headless `claude --print` doesn't auto-discover .mcp.json
        # reliably in recent CLI versions. Without an explicit
        # --mcp-config the worktree's project servers (Linear, GitHub,
        # whatever the user inherited) silently don't load. Maiko's
        # own comms don't depend on MCP (CLI + hooks cover everything),
        # but inherited project MCPs do.
        if mcp_config_path and _os.path.exists(mcp_config_path):
            cmd.extend(["--mcp-config", mcp_config_path])

        if model:
            cmd.extend(["--model", model])

        if effort in ("low", "medium", "high", "max"):
            cmd.extend(["--effort", effort])

        if permission_mode:
            cmd.extend(["--permission-mode", permission_mode])

        spawn_env = dict(_os.environ)
        if extra_env:
            spawn_env.update(extra_env)
        # ENABLE_PROMPT_CACHING_1H for long-running agent sessions when
        # the user opted in via brain.prompt_cache_1h. Same path the
        # synchronous send() uses.
        cache_env = self._build_subprocess_env()
        if cache_env is not None:
            spawn_env.update({k: v for k, v in cache_env.items() if k not in spawn_env})

        log_file = None
        popen = None
        crash_error = None
        try:
            if log_path:
                log_file = open(log_path, "w", encoding="utf-8")
                log_file.write(f"# Headless agent run\n# session_id: {session_id}\n\n")
                log_file.flush()
                stdout = log_file
                stderr = subprocess.STDOUT
            else:
                stdout = subprocess.PIPE
                stderr = subprocess.PIPE

            popen = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=working_dir,
                env=spawn_env,
            )

            # Cancellation registry — optional. The kickoff path passes
            # job_id so stop_agent_session can reach in and terminate;
            # one-off harnesses can omit it.
            if job_id:
                try:
                    from planet_maiko.agents.runtime.process import (
                        register_running_process,
                        unregister_running_process,
                    )
                    register_running_process(job_id, popen)
                except Exception:
                    pass

            try:
                popen.communicate(input=initial_prompt)
            finally:
                if job_id:
                    try:
                        unregister_running_process(job_id)
                    except Exception:
                        pass

            if popen.returncode not in (None, 0):
                crash_error = (
                    f"claude exited {popen.returncode}: "
                    + _tail_log(log_path) if log_path else
                    f"claude exited {popen.returncode}"
                )
        except Exception as e:
            crash_error = f"spawn failed: {e}"
        finally:
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    pass

        pid = popen.pid if popen is not None else None
        exit_code = popen.returncode if popen is not None else None
        return {
            "success": crash_error is None,
            "pid": pid,
            "exit_code": exit_code,
            "error": crash_error,
            "log_tail": _tail_log(log_path) if (log_path and crash_error) else None,
        }

    def resume(
        self,
        working_dir,
        session_id,
        prompt,
        *,
        job_id=None,
        log_path=None,
        model=None,
        effort=None,
        permission_mode=None,
        extra_env=None,
        extra_args=None,
    ):
        """Continue an existing claude session with new input.

        Maps to ``claude --print --resume <session_id>``. The
        transcript at ~/.claude/projects/.../<session_id>.jsonl is
        Claude's source of truth for prior conversation state — we
        just point at it and let claude reconstruct the context.

        See AgentRuntime.resume for the full contract.
        """
        if not prompt_has_text(prompt):
            return _spawn_error("Empty prompt — nothing to send")

        claude_path = self._find_claude()
        if not claude_path:
            return _spawn_error("claude CLI not found")

        cmd = [
            claude_path, "--print", "--output-format", "text",
            "--resume", session_id,
            "--dangerously-skip-permissions",
        ]

        if model:
            cmd.extend(["--model", model])
        if effort in ("low", "medium", "high", "max"):
            cmd.extend(["--effort", effort])
        if permission_mode:
            cmd.extend(["--permission-mode", permission_mode])
        if extra_args:
            cmd.extend(extra_args)

        spawn_env = dict(_os.environ)
        if extra_env:
            spawn_env.update(extra_env)
        cache_env = self._build_subprocess_env()
        if cache_env is not None:
            spawn_env.update({k: v for k, v in cache_env.items() if k not in spawn_env})

        log_file = None
        popen = None
        crash_error = None
        try:
            if log_path:
                # Append to the existing agent.log — resumes are
                # continuations of a session that already has a log.
                log_file = open(log_path, "a", encoding="utf-8")
                from datetime import datetime, timezone
                log_file.write(
                    f"\n\n# wake / resume at "
                    f"{datetime.now(timezone.utc).isoformat()}\n\n"
                )
                log_file.flush()
                stdout = log_file
                stderr = subprocess.STDOUT
            else:
                stdout = subprocess.PIPE
                stderr = subprocess.PIPE

            popen = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=working_dir,
                env=spawn_env,
            )

            # Same cancellation hook as spawn — wake_agent flows pass
            # a job_id; we register the Popen so stop_agent_session
            # can terminate mid-resume.
            if job_id:
                try:
                    from planet_maiko.agents.runtime.process import (
                        register_running_process,
                        unregister_running_process,
                    )
                    register_running_process(job_id, popen)
                except Exception:
                    pass

            try:
                popen.communicate(input=prompt)
            finally:
                if job_id:
                    try:
                        unregister_running_process(job_id)
                    except Exception:
                        pass

            if popen.returncode not in (None, 0):
                crash_error = (
                    f"claude --resume exited {popen.returncode}: "
                    + _tail_log(log_path) if log_path else
                    f"claude --resume exited {popen.returncode}"
                )
        except Exception as e:
            crash_error = f"resume failed: {e}"
        finally:
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    pass

        pid = popen.pid if popen is not None else None
        exit_code = popen.returncode if popen is not None else None
        return {
            "success": crash_error is None,
            "pid": pid,
            "exit_code": exit_code,
            "error": crash_error,
            "log_tail": _tail_log(log_path) if (log_path and crash_error) else None,
        }

    def session_transcript_path(self, session_id, working_dir=None):
        """Locate the JSONL transcript Claude wrote for this session.

        Claude Code stores transcripts at
        ``~/.claude/projects/{escaped-path}/{session_id}.jsonl``, where
        ``escaped-path`` replaces ``/``, ``\\``, and ``:`` with ``-``
        (so ``C:\\Users\\foo`` becomes ``C--Users-foo`` on Windows).
        Returns the first candidate that actually exists on disk, or
        None if neither does.
        """
        if not session_id or not working_dir:
            return None
        abs_path = _os.path.abspath(working_dir)
        escaped = abs_path.replace(":", "-").replace("\\", "-").replace("/", "-")
        candidates = [
            _os.path.expanduser(f"~/.claude/projects/{escaped}/{session_id}.jsonl"),
            _os.path.expanduser(f"~/.config/claude/projects/{escaped}/{session_id}.jsonl"),
        ]
        for path in candidates:
            if _os.path.isfile(path):
                return path
        return None


# ---------------------------------------------------------------------------
# Helpers used by spawn() / resume() — kept module-private so they don't
# leak into the runtime class's public surface.
# ---------------------------------------------------------------------------

def prompt_has_text(prompt):
    return bool(prompt and prompt.strip())


def _spawn_error(msg):
    return {
        "success": False,
        "pid": None,
        "exit_code": None,
        "error": msg,
        "log_tail": None,
    }


def _tail_log(path, max_chars=400):
    """Return the last ~max_chars of a log file, or a friendly placeholder."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        snippet = data[-max_chars:].strip()
        return snippet or "(empty log)"
    except Exception as e:
        return f"(could not read log: {e})"
