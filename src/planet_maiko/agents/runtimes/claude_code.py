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

    def is_available(self):
        return shutil.which("claude") is not None

    def send(self, prompt, working_dir=None, timeout=300):
        """Send a prompt to claude CLI in print mode.

        Uses --print for single prompt/response (no interactive session).
        This is used for brain triage and skill execution.
        """
        cmd = ["claude", "--print", "--output-format", "text", prompt]

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

    def send_json(self, prompt, working_dir=None, timeout=300):
        """Send a prompt and parse the response as JSON.

        Wraps the prompt with instructions to return JSON.
        Used for structured decisions (triage, task creation, etc.).
        """
        json_prompt = (
            f"{prompt}\n\n"
            "Respond with ONLY valid JSON, no markdown fencing, no explanation."
        )

        result = self.send(json_prompt, working_dir=working_dir, timeout=timeout)

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
                    ["claude", "--version"],
                    capture_output=True, text=True, timeout=5,
                )
                info["version"] = result.stdout.strip()
            except Exception:
                info["version"] = "unknown"
        return info
