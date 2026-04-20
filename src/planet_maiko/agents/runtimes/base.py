"""Base runtime interface for agent sessions.

A runtime is the engine that powers an agent - it accepts prompts,
reasons about them, optionally uses tools, and returns results.

Planet Maiko ships with a Claude Code runtime, but any agent that
implements this interface can be plugged in (a custom OpenAI wrapper,
a local Ollama agent, etc.).
"""

from abc import ABC, abstractmethod


class AgentRuntime(ABC):
    """Abstract interface for an agent runtime.

    Implementations must handle:
        - Sending a prompt to the agent
        - Collecting the response (which may include tool use)
        - Managing session lifecycle (start, stop, status)
    """

    @property
    @abstractmethod
    def name(self):
        """Unique name for this runtime (e.g. 'claude-code', 'aider')."""
        ...

    @abstractmethod
    def send(self, prompt, working_dir=None, timeout=300, model=None, allowed_tools=None):
        """Send a prompt to the agent and get a response.

        Args:
            prompt: The instruction/question for the agent
            working_dir: Directory the agent should work in (optional)
            timeout: Max seconds to wait for a response
            model: Model tier override (e.g. "haiku", "sonnet", "opus")
            allowed_tools: Optional list of tool identifiers to pre-
                authorize for this call. When None, the runtime falls
                back to its configured default (e.g.
                config.brain.allowed_tools). When provided, overrides —
                typically this is the base allowlist merged with any
                per-skill MCPs declared on the skill definition.

        Returns:
            dict with:
                - output: str (the agent's text response)
                - success: bool
                - error: str or None
        """
        ...

    @abstractmethod
    def is_available(self):
        """Check if this runtime is installed and usable.

        Returns:
            bool
        """
        ...

    def get_info(self):
        """Get runtime info for the dashboard.

        Returns:
            dict with name, version, status, etc.
        """
        return {
            "name": self.name,
            "available": self.is_available(),
        }
