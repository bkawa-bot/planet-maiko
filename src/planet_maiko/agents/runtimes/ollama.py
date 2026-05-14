"""OllamaRuntime: send-only AgentRuntime for local OpenAI-compatible
servers.

Targets Ollama by default (the most common local-model server), but
because it speaks the OpenAI-compatible `/v1/chat/completions`
endpoint it also works with vLLM, llama.cpp's server, LM Studio, and
any other server that implements the same protocol — just point
``ollama.base_url`` at it.

Purpose: route Maiko's "internal" LLM calls (overview generation,
agent bios, scene notes, etc.) to a local model instead of Anthropic.
For internal work, top-tier reasoning isn't required and the
local-cost is zero, so anything Maiko can do without Claude should
get done without Claude. Coding / review / investigation agents stay
on Claude (or claude-code-tmux) per task-type routing — those need
the tool-using agent loop Ollama doesn't provide.

This runtime doesn't implement spawn / resume. Sync-only. Any caller
that needs to launch an autonomous agent (kickoff.py / wake.py) will
naturally fall back to a runtime that does support spawn via the
default ``brain.runtime`` setting.

Config (config.yaml)::

    ollama:
      base_url: http://localhost:11434   # default
      default_model: llama3.1:8b         # used when routing doesn't
                                         # name a specific model

    routing:
      runtime_rules:
        scene: ollama
        skill:home-overview: ollama
        agent_bio: ollama
"""

import json
import logging
import urllib.error
import urllib.request

from planet_maiko.config import load_config

from .base import AgentRuntime

logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_MODEL = "llama3.1:8b"

# Maiko's routing layer hands every send() a model name resolved from
# the routing config — which today is keyed on Claude tier names
# (haiku / sonnet / opus). When a Claude-routed task gets bounced over
# to Ollama (e.g., user moved skill:home-overview to ollama in
# runtime_rules but didn't add a matching ollama-tier model), the
# call would otherwise fail with "model opus not found." Recognize
# the Claude names and substitute our default instead. Same pattern
# for any prefix we treat as Anthropic-specific. Long-term fix is
# per-runtime model rules in routing config; this is the safety net.
_CLAUDE_TIER_NAMES = frozenset({"haiku", "sonnet", "opus"})


def _is_claude_model_name(model):
    if not model:
        return False
    if model in _CLAUDE_TIER_NAMES:
        return True
    if model.startswith("claude-") or model.startswith("claude_"):
        return True
    return False

# Claude's `--effort` knob controls extended thinking, which open-
# source models don't expose. Map it to sampling temperature + a
# token budget so the cheap/fast vs. richer/slower distinction
# carries over conceptually. Tweak in the constants here; the
# routing system passes us the effort string from config.
_EFFORT_MAP = {
    "low":    {"temperature": 0.2, "max_tokens": 1500},
    "medium": {"temperature": 0.5, "max_tokens": 4000},
    "high":   {"temperature": 0.7, "max_tokens": 8000},
    "max":    {"temperature": 0.7, "max_tokens": 16000},
}


class OllamaRuntime(AgentRuntime):
    """Local OpenAI-compatible runtime. send / send_json only."""

    name = "ollama"

    # ----- Availability + introspection -----

    def is_available(self):
        """Ping ``/api/tags`` (cheap, no LLM work) to confirm the
        server is reachable and serving."""
        try:
            req = urllib.request.Request(
                f"{self._base_url()}/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                return getattr(resp, "status", 200) == 200
        except Exception:
            return False

    def get_info(self):
        info = super().get_info()
        info["base_url"] = self._base_url()
        if info["available"]:
            try:
                models = self._list_models()
                info["models"] = models[:10]
                info["model_count"] = len(models)
            except Exception:
                pass
        return info

    # ----- Synchronous send -----

    def send(
        self,
        prompt,
        working_dir=None,
        timeout=300,
        model=None,
        allowed_tools=None,        # ignored — Ollama doesn't run tools
        session_id=None,           # ignored — Ollama is stateless from our side
        skip_permissions=False,    # ignored
        permission_mode=None,      # ignored
        effort=None,
    ):
        """Run ``prompt`` against the local model. Returns the same
        ``{output, success, error}`` shape as ClaudeCodeRuntime.send.

        Unknown flags (allowed_tools, session_id, etc.) are silently
        dropped — they don't apply to a single-shot chat completion.
        Callers may pass them generically without special-casing this
        runtime.
        """
        if not prompt or not prompt.strip():
            return {
                "output": "",
                "success": False,
                "error": "Empty prompt — nothing to send",
            }

        body = self._compose_body(prompt, model=model, effort=effort)
        return self._chat_completion(body, timeout=timeout)

    def send_json(
        self,
        prompt,
        working_dir=None,
        timeout=300,
        model=None,
        allowed_tools=None,
        permission_mode=None,
        effort=None,
    ):
        """Run ``prompt`` requesting JSON output. Uses
        ``response_format`` to force structured output where the
        server supports it (Ollama does; vLLM does; LM Studio does
        with some models). Parses the response and adds ``parsed``
        to the result.
        """
        if not prompt or not prompt.strip():
            return {
                "output": "",
                "success": False,
                "error": "Empty prompt — nothing to send",
                "parsed": None,
            }

        body = self._compose_body(
            prompt + "\n\nRespond with ONLY valid JSON. No markdown fencing, no explanation.",
            model=model,
            effort=effort,
        )
        body["response_format"] = {"type": "json_object"}

        result = self._chat_completion(body, timeout=timeout)
        if not result["success"]:
            result["parsed"] = None
            return result

        output = (result.get("output") or "").strip()
        # Strip markdown fencing in case the model added it anyway.
        if output.startswith("```"):
            lines = output.split("\n")
            if lines and lines[-1].startswith("```"):
                output = "\n".join(lines[1:-1])
            else:
                output = "\n".join(lines[1:])

        try:
            result["parsed"] = json.loads(output)
        except json.JSONDecodeError as e:
            logger.warning(f"[ollama] JSON parse failed: {e}")
            result["parsed"] = None
            result["error"] = f"Invalid JSON: {e}"

        return result

    # ----- helpers -----

    def _config(self):
        try:
            return load_config().get("ollama") or {}
        except Exception:
            return {}

    def _base_url(self):
        return self._config().get("base_url") or _DEFAULT_BASE_URL

    def _default_model(self):
        return self._config().get("default_model") or _DEFAULT_MODEL

    def _list_models(self):
        try:
            req = urllib.request.Request(
                f"{self._base_url()}/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
            return [
                m.get("name") for m in (data.get("models") or [])
                if m.get("name")
            ]
        except Exception:
            return []

    def _compose_body(self, prompt, *, model=None, effort=None):
        eff = _EFFORT_MAP.get(effort or "medium", _EFFORT_MAP["medium"])
        # Map Claude-tier names (handed in by routing.resolve_model)
        # to our default. Logs once at info level the first time it
        # happens for a given tier so the user knows the routing
        # config still resolves to Claude-side names somewhere.
        if _is_claude_model_name(model):
            logger.info(
                f"[ollama] ignoring Claude-tier model {model!r}; using "
                f"default {self._default_model()!r}. Add per-runtime model "
                f"rules in routing.runtime_models if you want a "
                f"different Ollama model per task."
            )
            model = None
        return {
            "model": model or self._default_model(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": eff["temperature"],
            "max_tokens": eff["max_tokens"],
            "stream": False,
        }

    def _chat_completion(self, body, timeout):
        url = f"{self._base_url()}/v1/chat/completions"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            data = json.loads(raw)
            output = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return {
                "output": (output or "").strip(),
                "success": True,
                "error": None,
            }
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode(errors="replace")[:500]
            except Exception:
                err_body = str(e)
            logger.warning(f"[ollama] HTTP {e.code} from {url}: {err_body[:200]}")
            return {
                "output": "",
                "success": False,
                "error": f"HTTP {e.code}: {err_body}",
            }
        except urllib.error.URLError as e:
            return {
                "output": "",
                "success": False,
                "error": f"Couldn't reach Ollama at {url}: {e.reason}",
            }
        except Exception as e:
            logger.warning(f"[ollama] unexpected error: {e}")
            return {
                "output": "",
                "success": False,
                "error": str(e),
            }
