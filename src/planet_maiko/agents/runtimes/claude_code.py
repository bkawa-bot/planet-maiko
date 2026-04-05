"""Claude Code runtime - runs prompts through the claude CLI.

This is the default runtime for Planet Maiko. It spawns claude CLI
processes to handle agent work, leveraging Claude Code's built-in
tool use, MCP integrations, and file system access.

For the brain session, we use --print mode for quick prompt/response.
For coding agents, we use full interactive sessions in git worktrees.
"""

import json
import logging
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
        """Load allowed tools from config."""
        try:
            from planet_maiko.config import load_config
            config = load_config()
            return config.get("brain", {}).get("allowed_tools", [])
        except Exception:
            return []

    def send(self, prompt, working_dir=None, timeout=300, model=None):
        """Send a prompt to claude CLI in print mode.

        Uses --print for single prompt/response (no interactive session).
        This is used for brain triage and skill execution.
        """
        claude_path = self._find_claude() or "claude"
        cmd = [claude_path, "--print", "--output-format", "text"]

        # Model override for cost-aware routing
        if model:
            cmd.extend(["--model", model])

        # Pre-approve tools to avoid permission prompts
        allowed = self._get_allowed_tools()
        for tool in allowed:
            cmd.extend(["--allowedTools", tool])

        cmd.append(prompt)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=working_dir,
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

    def send_json(self, prompt, working_dir=None, timeout=300, model=None):
        """Send a prompt and parse the response as JSON.

        Wraps the prompt with instructions to return JSON.
        Used for structured decisions (triage, task creation, etc.).
        """
        json_prompt = (
            f"{prompt}\n\n"
            "Respond with ONLY valid JSON, no markdown fencing, no explanation."
        )

        result = self.send(json_prompt, working_dir=working_dir, timeout=timeout, model=model)

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
