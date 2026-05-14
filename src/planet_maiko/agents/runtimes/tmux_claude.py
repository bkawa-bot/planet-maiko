"""Tmux-driven ClaudeCodeRuntime.

Runs ``claude`` interactively inside a tmux session instead of
``claude --print`` headless. The motivation is billing: Anthropic
splits agentic / Agent-SDK usage (which ``--print`` falls into) from
interactive Claude Code usage (which the TUI falls into). For users
on a Max 5x plan, the interactive pool is much larger than the
$100/month Agent SDK credit. By driving claude through a real
terminal (a tmux pane), Maiko's long-running coding agents bill
against the right pool.

Mac-only for the first pass. Tmux is universally available via
Homebrew on Mac, paths are POSIX-clean, no ConPTY weirdness. Linux
support is straightforward (same tmux); Windows would need WSL or
ConPTY-aware refactoring.

Session lifecycle (the per-turn model, not keep-alive):

  1. spawn() or resume() creates a tmux session named ``maiko-<job_id>``,
     starts ``claude`` inside it (the foreground process so the
     session dies if claude crashes), pipe-panes the output to
     agent.log, sends the prompt, and blocks until the session ends.
  2. The agent works. Tool calls, ``maiko reply --type status``
     updates, etc.
  3. When the agent emits a terminal-typed reply (``ready_for_review``,
     ``stuck``, ``plan_for_approval``, ``pr_opened``), the outbox
     handler calls ``runtime.end_session(job_id)`` which kills the
     tmux session.
  4. spawn()/resume() unblocks and returns. The kickoff / wake daemon
     thread releases its lock, flips agent state to idle.

Conversation persistence lives on disk in the JSONL transcript
(``~/.claude/projects/.../<session_id>.jsonl``), exactly the same as
the headless path. Re-opening a session = new tmux + ``claude
--resume <session_id>``. No tokens are charged for the local replay.

Inherits ``send`` / ``send_json`` from ClaudeCodeRuntime — those are
short fire-and-forget skill / chat / triage calls. Routing them
through tmux is more plumbing than payoff (their volume is small
compared to coding agent runs).
"""

import logging
import os as _os
import shutil
import subprocess
import threading
import time

from .claude_code import (
    ClaudeCodeRuntime,
    _spawn_error,
    _tail_log,
    prompt_has_text,
)

logger = logging.getLogger(__name__)


# How long to wait for claude's interactive TUI to be ready before
# sending the initial prompt. Short poll loop; we accept the prompt
# either when we see something prompt-like in the pane, or when the
# timeout elapses (claude is probably ready by then anyway).
_READY_TIMEOUT_S = 5.0
_READY_POLL_S = 0.2

# Poll cadence for "is the tmux session still alive?" while spawn /
# resume blocks. 1s is fine — agent turns are seconds-to-minutes.
_TURN_POLL_S = 1.0

# Tmux session name prefix. Used by cleanup_orphan_sessions to find
# Maiko-owned sessions across server restarts.
_SESSION_PREFIX = "maiko-"


class TmuxClaudeRuntime(ClaudeCodeRuntime):
    """ClaudeCodeRuntime variant that runs spawn / resume through an
    interactive tmux pane instead of ``claude --print`` headless."""

    name = "claude-code-tmux"

    # Map {job_id: tmux_session_name} for currently-active turns.
    # Mutated only via the helper methods below so concurrent
    # spawn/resume/end_session calls don't trip each other.
    _SESSIONS_GUARD = threading.Lock()
    _SESSIONS = {}

    # ----- Availability -----

    def is_available(self):
        if not super().is_available():
            return False
        return shutil.which("tmux") is not None

    def get_info(self):
        info = super().get_info()
        info["name"] = self.name
        info["tmux"] = shutil.which("tmux") is not None
        return info

    # ----- spawn / resume -----

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
        return self._run_turn(
            mode="new",
            working_dir=working_dir,
            session_id=session_id,
            prompt=initial_prompt,
            job_id=job_id,
            mcp_config_path=mcp_config_path,
            log_path=log_path,
            model=model,
            effort=effort,
            permission_mode=permission_mode,
            extra_env=extra_env,
        )

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
        # extra_args (used by plan-revise on the headless path) gets
        # mapped into the claude CLI invocation we send to tmux.
        return self._run_turn(
            mode="resume",
            working_dir=working_dir,
            session_id=session_id,
            prompt=prompt,
            job_id=job_id,
            mcp_config_path=None,  # resume doesn't re-add --mcp-config
            log_path=log_path,
            model=model,
            effort=effort,
            permission_mode=permission_mode,
            extra_env=extra_env,
            extra_args=extra_args,
        )

    # Shared implementation for spawn + resume. The two differ only in
    # whether claude is invoked with --session-id (new) or --resume
    # (existing). Everything else — tmux session lifecycle, prompt
    # injection, log piping, blocking wait — is identical.
    def _run_turn(
        self,
        *,
        mode,
        working_dir,
        session_id,
        prompt,
        job_id,
        mcp_config_path,
        log_path,
        model,
        effort,
        permission_mode,
        extra_env,
        extra_args=None,
    ):
        if not prompt_has_text(prompt):
            return _spawn_error("Empty prompt — nothing to send")
        if not job_id:
            return _spawn_error("TmuxClaudeRuntime requires job_id for session tracking")
        if not self._find_claude():
            return _spawn_error("claude CLI not found")
        if not shutil.which("tmux"):
            return _spawn_error("tmux not found (Homebrew: brew install tmux)")

        sess = _session_name(job_id)
        claude_cmd = self._compose_claude_cmd(
            session_id=session_id,
            mode=mode,
            model=model,
            effort=effort,
            permission_mode=permission_mode,
            mcp_config_path=mcp_config_path,
            extra_args=extra_args,
        )

        env_kv = self._compose_env(extra_env)

        created = self._tmux_create(
            sess=sess,
            working_dir=working_dir,
            env_kv=env_kv,
            claude_cmd=claude_cmd,
        )
        if not created:
            return _spawn_error(f"tmux new-session failed for {sess}")

        with self._SESSIONS_GUARD:
            self._SESSIONS[job_id] = sess

        # Pipe pane → log file so we keep the existing agent.log
        # surface. -o appends to existing output rather than
        # truncating, so resumes don't clobber spawn's header.
        if log_path:
            try:
                self._tmux_pipe_pane(sess, log_path, append=(mode == "resume"))
            except Exception as e:
                logger.warning(f"[tmux-claude] pipe-pane failed for {sess}: {e}")

        # Wait for claude to draw its initial prompt before we feed
        # input. Best-effort; we proceed on timeout because claude
        # almost always is ready by then.
        ready = self._wait_for_ready(sess)
        if not ready and not self._tmux_alive(sess):
            self._cleanup(sess, job_id)
            return _spawn_error("claude crashed before becoming ready (see agent.log)")

        # Send the prompt as if user typed it. -l = literal, so
        # special chars in the prompt aren't interpreted as tmux
        # keysyms ("C-c" etc).
        self._tmux_send(sess, prompt)

        # Block until the session ends. The terminating event is
        # either: outbox handler called end_session() because the
        # agent posted a terminal-typed maiko reply, OR cancellation
        # killed the session, OR claude crashed (session dies with
        # the foreground process).
        self._wait_for_session_end(sess)

        # Best-effort cleanup in case end_session wasn't the path that
        # killed us (e.g., claude crash).
        with self._SESSIONS_GUARD:
            self._SESSIONS.pop(job_id, None)

        return {
            "success": True,
            "pid": None,
            "exit_code": 0,
            "error": None,
            "log_tail": None,
        }

    # ----- end_session: outbox-triggered teardown -----

    def end_session(self, job_id):
        """Kill the tmux session bound to this job, if one's active.

        Called by ``handle_agent_job_reply`` when the agent posts a
        terminal-typed reply. Idempotent: if there's no live session
        for this job, this is a no-op.
        """
        with self._SESSIONS_GUARD:
            sess = self._SESSIONS.pop(job_id, None)
        if not sess:
            return
        self._tmux_kill(sess)

    # ----- helpers -----

    def _compose_claude_cmd(
        self,
        *,
        session_id,
        mode,
        model,
        effort,
        permission_mode,
        mcp_config_path,
        extra_args,
    ):
        """Build the ``claude ...`` command string to send into tmux.

        Critically, no ``--print``. The whole point of running through
        tmux is that claude operates as an interactive TUI process,
        which is what bills against the user's subscription pool
        rather than the Agent SDK credit.
        """
        claude_path = self._find_claude() or "claude"
        parts = [_shlex_quote(claude_path)]

        if mode == "new":
            parts.extend(["--session-id", session_id])
        elif mode == "resume":
            parts.extend(["--resume", session_id])

        parts.append("--dangerously-skip-permissions")

        if mcp_config_path and _os.path.exists(mcp_config_path):
            parts.extend(["--mcp-config", _shlex_quote(mcp_config_path)])
        if model:
            parts.extend(["--model", model])
        if effort in ("low", "medium", "high", "max"):
            parts.extend(["--effort", effort])
        if permission_mode:
            parts.extend(["--permission-mode", permission_mode])
        if extra_args:
            parts.extend(extra_args)

        return " ".join(parts)

    def _compose_env(self, extra_env):
        """Build a {key: val} dict of env vars to pass into the tmux
        session. tmux forwards these to the foreground claude process
        and any child commands the agent spawns from the pane."""
        env = {}
        if extra_env:
            env.update(extra_env)
        # Inherit the prompt-caching env from ClaudeCodeRuntime's
        # subprocess-env builder so the 1-hour cache extension flows
        # to tmux-driven runs same as headless runs.
        cache_env = self._build_subprocess_env()
        if cache_env:
            for k, v in cache_env.items():
                if k.startswith("ENABLE_") or k.startswith("ANTHROPIC_"):
                    env.setdefault(k, v)
        return env

    def _tmux_create(self, sess, working_dir, env_kv, claude_cmd):
        """Create the tmux session with claude as the foreground
        command. Pass env vars via -e so they flow to claude + any
        child shells the agent spawns.

        Using claude as the session's foreground command (rather than
        starting a shell and then running claude inside it) means the
        session naturally dies when claude exits — giving us free
        crash detection.
        """
        cmd = ["tmux", "new-session", "-d", "-s", sess, "-c", working_dir]
        for k, v in env_kv.items():
            cmd.extend(["-e", f"{k}={v}"])
        # The claude invocation goes last, as a single argv entry that
        # tmux runs in its default shell.
        cmd.append(claude_cmd)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.warning(
                    f"[tmux-claude] new-session failed: "
                    f"{(result.stderr or '').strip()[:200]}"
                )
                return False
        except Exception as e:
            logger.warning(f"[tmux-claude] new-session crashed: {e}")
            return False
        return True

    def _tmux_pipe_pane(self, sess, log_path, append=False):
        """Tell tmux to copy everything that hits the pane to a file.

        -o = pipe regardless of prior pipe-pane state (idempotent).
        Append mode uses ``cat >>`` so resume calls add to the
        existing agent.log rather than truncating spawn's header.
        """
        redir = ">>" if append else ">"
        # shell quoting: log_path is under our control (worktree
        # path), but quote anyway for paths with spaces.
        cmd = [
            "tmux", "pipe-pane", "-o", "-t", sess,
            f"cat {redir} {_shlex_quote(log_path)}",
        ]
        subprocess.run(cmd, capture_output=True, timeout=5)

    def _tmux_send(self, sess, text):
        """Type ``text`` into the pane as if a user did, followed by
        Enter. -l ensures special characters aren't interpreted as
        tmux keysyms.

        Long prompts: tmux send-keys handles megabytes fine; the
        practical limit is the OS argv cap (~32K on Windows, much
        higher on Mac/Linux). For the rare prompt that exceeds that,
        we'd need to chunk; not bothering until it bites.
        """
        try:
            subprocess.run(
                ["tmux", "send-keys", "-t", sess, "-l", text],
                capture_output=True, timeout=10,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", sess, "Enter"],
                capture_output=True, timeout=5,
            )
        except Exception as e:
            logger.warning(f"[tmux-claude] send-keys to {sess} failed: {e}")

    def _tmux_capture(self, sess):
        """Read the current pane content as plain text (no ANSI)."""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", sess, "-p"],
                capture_output=True, text=True, timeout=3,
            )
            return result.stdout or ""
        except Exception:
            return ""

    def _tmux_alive(self, sess):
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", sess],
                capture_output=True, timeout=3,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _tmux_kill(self, sess):
        try:
            subprocess.run(
                ["tmux", "kill-session", "-t", sess],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    def _wait_for_ready(self, sess):
        """Poll the pane until claude's TUI looks ready for input.

        Heuristic: capture the pane every _READY_POLL_S and look for
        common prompt indicators (``>``, ``?``, ``Welcome to Claude``,
        etc.). Best-effort: returns True on success, True on timeout
        (assume claude is ready), False only if the session died
        before becoming ready.
        """
        deadline = time.time() + _READY_TIMEOUT_S
        while time.time() < deadline:
            time.sleep(_READY_POLL_S)
            if not self._tmux_alive(sess):
                return False
            content = self._tmux_capture(sess)
            if content and any(marker in content for marker in (
                ">", "?", "Welcome to Claude",
                "Try \"", "Try `", "Press Enter",
            )):
                return True
        return True  # claude is almost certainly ready by now

    def _wait_for_session_end(self, sess):
        """Block until the tmux session no longer exists.

        Polling every _TURN_POLL_S keeps the daemon thread's wake
        cadence cheap. The session ends when either:
          (a) end_session() killed it after a terminal-typed reply
          (b) cancellation killed it
          (c) claude crashed (we used claude as the session's
              foreground so the session dies with the process)
        """
        while self._tmux_alive(sess):
            time.sleep(_TURN_POLL_S)

    def _cleanup(self, sess, job_id):
        with self._SESSIONS_GUARD:
            self._SESSIONS.pop(job_id, None)
        self._tmux_kill(sess)


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _session_name(job_id):
    return f"{_SESSION_PREFIX}{job_id}"


def _shlex_quote(s):
    """Quote a value for inclusion in a shell command string.

    Used when composing the claude command we hand to tmux (tmux runs
    it through its default shell). Imported lazily so the runtime
    module loads cheaply.
    """
    import shlex
    return shlex.quote(str(s))


def cleanup_orphan_sessions():
    """Kill any ``maiko-*`` tmux sessions whose AgentJob is in a
    terminal state (or doesn't exist anymore).

    Called once at Maiko startup to clean up after crashes. Safe to
    call when tmux isn't installed or no server is running — both
    fail closed.
    """
    if not shutil.which("tmux"):
        return 0
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return 0
    except Exception:
        return 0

    sessions = [
        line.strip() for line in result.stdout.splitlines()
        if line.strip().startswith(_SESSION_PREFIX)
    ]
    if not sessions:
        return 0

    killed = 0
    try:
        from planet_maiko.database import db
        from planet_maiko.models.agent_job import AgentJob
    except Exception:
        # DB not available — kill them all conservatively, on the
        # assumption that a Maiko-prefixed session whose owner we
        # can't verify shouldn't be left running.
        for sess in sessions:
            try:
                subprocess.run(
                    ["tmux", "kill-session", "-t", sess],
                    capture_output=True, timeout=3,
                )
                killed += 1
            except Exception:
                pass
        return killed

    for sess in sessions:
        job_id = sess[len(_SESSION_PREFIX):]
        try:
            job = db.session.get(AgentJob, job_id)
        except Exception:
            job = None
        terminal = job is None or job.status in ("done", "failed", "cancelled")
        if not terminal:
            continue
        try:
            subprocess.run(
                ["tmux", "kill-session", "-t", sess],
                capture_output=True, timeout=3,
            )
            logger.info(f"[tmux-claude] cleanup killed orphan session: {sess}")
            killed += 1
        except Exception:
            pass
    return killed
