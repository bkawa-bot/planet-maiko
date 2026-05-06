"""File-authoring helpers for the coding-agent worktree:
TASK.md, CLAUDE.md, .mcp.json, and the .claude/settings.local.json
hook config. All write into the worktree before kickoff."""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _write_task_file(working_path, task_id, task_title, prompt):
    """Write TASK.md so the agent knows what to do."""
    # Human-readable file — show the user's local time, not UTC. Agents
    # and users both read this; a "Created: 23:30 UTC" line is confusing
    # when the user thinks of it as 3:30pm Pacific.
    from planet_maiko.config import user_now
    content = f"""# Task: {task_title}

**Task ID:** {task_id}
**Created:** {user_now().strftime('%Y-%m-%d %H:%M %Z')}

## Instructions

{prompt}
"""
    with open(os.path.join(working_path, "TASK.md"), "w", encoding="utf-8") as f:
        f.write(content)


def _write_claude_md(working_path, task_id, task_title, role="coding", maiko_port=None, parent_repo_path=None, agent_profile_id=None, specialty_id=None):
    """Write CLAUDE.md with full agent protocol.

    Loads the protocol template for the given role. "coding" uses the
    full agent-protocol (MCP channel, task-state reporting, etc.);
    review/investigation use their own protocol prompts which describe
    the structured-block output contract their initial one-shot run
    follows.

    parent_repo_path drives the Insights injection — active, non-
    expired Insights scoped to that repo (or global) get appended.
    Insights tagged `overview` get hoisted into a top-level `Repo
    Overview` H2 block (the cold-start map); the rest land as the
    usual "Team Playbook" bullet list.

    agent_profile_id, when set, pulls the agent's personal
    `instructions` field off their AgentProfile and appends it as a
    per-agent "Your Notes" section — carry-forward context so the
    agent doesn't re-learn the same things every session.

    specialty_id, when set, appends a CustomSkill's prompt as a
    "Your specialty for this run" section. Specialties are chosen
    per-run (by automation action_config, the assign modal, or a
    one-shot spawn) from the AgentProfile.specialty_ids pool. None
    = run on base role protocol only.
    """
    if maiko_port is None:
        from planet_maiko.config import MAIKO_PORT
        maiko_port = MAIKO_PORT

    role_instructions_for_role = ""
    try:
        from planet_maiko.config import load_config
        agents_cfg = load_config().get("agents", {}) or {}
        role_instructions_for_role = (agents_cfg.get("role_instructions") or {}).get(role, "") or ""
    except Exception:
        pass

    protocol_skill = {
        "review": "review-agent-protocol",
        "investigation": "investigation-agent-protocol",
        "cartographer": "cartographer-agent-protocol",
    }.get(role, "agent-protocol")

    # Agent identity + signature — filled into the protocol template so
    # the agent knows its own name (for first-person self-reference in
    # PR comments) and the exact sign-off line to append on external
    # posts. When the profile can't be resolved we use a grammatical
    # fallback so the protocol still reads sensibly, plus the protocol
    # tells agents to skip the sign-off in that case.
    agent_identity = "an unnamed agent"
    agent_signature = ""
    try:
        from planet_maiko.agents.signature import (
            format_agent_signature, format_agent_identity,
        )
        resolved_identity = format_agent_identity(agent_profile_id)
        if resolved_identity:
            agent_identity = resolved_identity
        agent_signature = format_agent_signature(agent_profile_id) or ""
    except Exception:
        pass

    # Load protocol from skill prompt (editable via Skills page)
    content = None
    try:
        from planet_maiko.agents.skills import get_skill_prompt
        content = get_skill_prompt(protocol_skill, {
            "task_title": task_title,
            "task_id": task_id,
            "maiko_port": str(maiko_port),
            "agent_identity": agent_identity,
            "agent_signature": agent_signature,
        })
    except Exception:
        pass

    # Fallback to the prompt file directly
    if not content:
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "prompts", f"{protocol_skill}.md"
        )
        try:
            with open(prompt_path, "r") as f:
                content = f.read()
            content = content.replace("{task_title}", task_title)
            content = content.replace("{task_id}", task_id)
            content = content.replace("{maiko_port}", str(maiko_port))
            content = content.replace("{agent_identity}", agent_identity)
            content = content.replace("{agent_signature}", agent_signature)
        except Exception:
            content = f"# Agent Protocol\n\nTask: {task_title}\nTask ID: {task_id}\nRole: {role}\n\nRead TASK.md for instructions."

    if role_instructions_for_role:
        content += f"\n\n## Team instructions for {role} agents\n\n{role_instructions_for_role.strip()}\n"

    # Active Insights for this repo (and globals). Unlike Learnings,
    # Insights aren't confidence-gated or trainable — they're the
    # "things every new agent in this repo should know" playbook:
    # tooling quirks, mid-migration state, team conventions that
    # aren't code rules. Insights tagged "overview" get promoted to
    # a Repo Overview block at the top; the rest render as the usual
    # Team Playbook bullets.
    playbook = _build_playbook_section(parent_repo_path)
    if playbook:
        content += f"\n\n{playbook}\n"

    agent_notes = _build_agent_notes_section(agent_profile_id)
    if agent_notes:
        content += f"\n\n{agent_notes}\n"

    specialty_block = _build_specialty_section(specialty_id, agent_profile_id)
    if specialty_block:
        content += f"\n\n{specialty_block}\n"

    claude_dir = os.path.join(working_path, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    with open(os.path.join(working_path, "CLAUDE.md"), "w", encoding="utf-8") as f:
        f.write(content)


def _build_playbook_section(parent_repo_path):
    """Render the Repo Overview + Team Playbook sections from active
    Insights scoped to this repo (or global).

    Thin wrapper around brain.learning.playbook.build_playbook — kept
    so existing call sites get the string form they expect. The
    underlying function also returns the structured insight list, which
    the read-surface HTTP endpoint uses.
    """
    from planet_maiko.brain.learning.playbook import build_playbook
    return build_playbook(parent_repo_path)["playbook_md"]


def _build_agent_notes_section(agent_profile_id):
    """Render the agent's personal Your Notes section from their
    AgentProfile.instructions — carry-forward context distinct from
    the shared-per-repo playbook above.

    Best-effort: missing profile or empty instructions returns "".
    """
    if not agent_profile_id:
        return ""
    try:
        from planet_maiko.database import db
        from planet_maiko.models.agent_profile import AgentProfile
        profile = db.session.get(AgentProfile, agent_profile_id)
        if not profile or not (profile.instructions or "").strip():
            return ""
        return (
            "## Your Notes\n\n"
            "Things you personally learned in past sessions on this or "
            "adjacent work. Review before starting.\n\n"
            f"{profile.instructions.strip()}"
        )
    except Exception as e:
        logger.debug(f"[claude_md] agent notes skipped: {e}")
        return ""


def _build_specialty_section(specialty_id, agent_profile_id):
    """Render the CustomSkill prompt as a "Your specialty for this run"
    section. Only kicks in when specialty_id is set AND it's one of the
    specialties attached to this agent's profile — guards against callers
    passing a stray id the agent was never supposed to run with.
    """
    if not specialty_id:
        return ""
    try:
        from planet_maiko.database import db
        from planet_maiko.models.custom_skill import CustomSkill
        from planet_maiko.models.agent_profile import AgentProfile

        if agent_profile_id:
            profile = db.session.get(AgentProfile, agent_profile_id)
            attached = set(profile.specialty_ids or []) if profile else set()
            if specialty_id not in attached:
                logger.info(
                    f"[claude_md] specialty {specialty_id} not attached to "
                    f"agent {agent_profile_id}; skipping injection."
                )
                return ""

        skill = db.session.get(CustomSkill, specialty_id)
        if not skill or not (skill.prompt or "").strip():
            return ""
        return (
            "## Your specialty for this run\n\n"
            f"This run picked **{skill.name}** — extra context on top of "
            "your base role protocol.\n\n"
            f"{skill.prompt.strip()}"
        )
    except Exception as e:
        logger.debug(f"[claude_md] specialty section skipped: {e}")
        return ""


def _inherit_mcp_servers(parent_repo_path):
    """Pull MCP server defs the user has configured for the parent repo
    plus their global set, so they're available inside the worktree.

    Without this, an agent in <parent>/.maiko-worktrees/<branch>/ only
    sees the maiko-channel MCP we wrote ourselves — Claude Code keys
    project-specific MCPs by absolute path, and the worktree's path
    doesn't match the parent's. Linear / Slack / GitHub / etc. that
    work in the user's normal session silently disappear in agent
    sessions, which is the "some MCP tools aren't available" report.

    Reads ~/.claude.json (the canonical store) and pulls:
      - top-level mcpServers (globals — should be available everywhere
        already, but bundling them is harmless and makes the worktree
        config self-contained)
      - projects.<parent_repo_path>.mcpServers (the per-repo set the
        user enabled when they were working in the parent)

    Returns a dict { name: server_config }, possibly empty.
    """
    import json as _json
    if not parent_repo_path:
        return {}
    parent_abs = os.path.abspath(parent_repo_path)
    config_path = os.path.expanduser("~/.claude.json")
    if not os.path.isfile(config_path):
        return {}
    try:
        with open(config_path, encoding="utf-8") as f:
            data = _json.load(f)
    except Exception:
        return {}

    inherited = {}
    globals_ = data.get("mcpServers") or {}
    if isinstance(globals_, dict):
        inherited.update(globals_)

    projects = data.get("projects") or {}
    if isinstance(projects, dict):
        # Match either the exact path or a normalized variant — Claude
        # Code sometimes stores paths with trailing slashes / different
        # case on Windows.
        for key, proj in projects.items():
            if not isinstance(proj, dict):
                continue
            try:
                key_abs = os.path.abspath(key)
            except Exception:
                continue
            if key_abs.lower().rstrip(os.sep) != parent_abs.lower().rstrip(os.sep):
                continue
            proj_servers = proj.get("mcpServers") or {}
            if isinstance(proj_servers, dict):
                inherited.update(proj_servers)
            break
    return inherited


def _write_mcp_json(working_path, task_id, parent_repo_path=None):
    """Write .mcp.json so maiko-channel + the user's parent-repo MCPs
    auto-load when claude starts inside the worktree.

    parent_repo_path lets us inherit the per-project MCPs the user had
    enabled in the parent repo (Linear / Slack / GitHub / etc.) so the
    agent has the same MCP surface area as the user's normal session.
    Without it the agent only sees maiko-channel.
    """
    import json

    # Find the channel script path relative to the planet-maiko repo root.
    # __file__ is src/planet_maiko/agents/runtime/scaffold.py — five
    # dirname() calls lands at the repo root. The previous count (four)
    # stopped at src/, fell through to a working_path-based fallback
    # whose unnormalized "..\..\" segments confused users reading the
    # generated .mcp.json. Both branches now normpath so the path
    # written into .mcp.json is always clean and absolute.
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    )))))
    channel_path = os.path.normpath(os.path.join(repo_root, "channel", "index.mjs"))

    # Fall back to looking relative to the working path. Worktrees
    # always live at <repo>/.maiko-worktrees/<branch>, so going up
    # two levels lands at the repo root that holds channel/.
    if not os.path.exists(channel_path):
        channel_path = os.path.normpath(
            os.path.join(working_path, "..", "..", "channel", "index.mjs")
        )

    from planet_maiko.config import maiko_api_url

    # Start with everything inherited from the parent repo / globals,
    # then layer maiko-channel on top so our entry always wins.
    servers = _inherit_mcp_servers(parent_repo_path)
    servers["maiko-channel"] = {
        "command": "node",
        "args": [channel_path],
        "env": {
            "MAIKO_TASK_ID": task_id,
            "MAIKO_API_URL": maiko_api_url(),
            "MAIKO_POLL_MS": "60000",
        },
    }

    mcp_config = {"mcpServers": servers}

    with open(os.path.join(working_path, ".mcp.json"), "w") as f:
        json.dump(mcp_config, f, indent=2)

    if len(servers) > 1:
        logger.info(
            f"[agent] Wrote .mcp.json with {len(servers)} server(s) "
            f"({sorted(servers.keys())})"
        )


def _write_claude_settings(working_path, task_id, agent_id):
    """Write .claude/settings.json (hooks config) and .maiko-env.json (identity).

    Checks the hooks config to determine which hooks are enabled before
    including them in settings.json.
    """
    import json

    # Resolve hooks directory — same five-dirname walk as _write_mcp_json
    # to land at the repo root, then `/hooks`. normpath cleans up any
    # `..` segments from the fallback before the path lands in
    # settings.json so the generated config reads cleanly.
    hooks_dir = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))))), "hooks"))

    # Fall back to looking relative to the working path
    if not os.path.isdir(hooks_dir):
        hooks_dir = os.path.normpath(
            os.path.join(working_path, "..", "..", "hooks")
        )

    # Normalize to absolute path with forward slashes for cross-platform compat
    hooks_dir = os.path.abspath(hooks_dir)

    # Load hooks config
    try:
        from planet_maiko.config import load_config
        config = load_config()
        hooks_config = config.get("hooks", {})
    except Exception:
        hooks_config = {"enabled": True}

    if not hooks_config.get("enabled", True):
        return

    # Build hooks dict, only including enabled hooks
    hooks = {}

    if hooks_config.get("post_tool_use", True):
        hooks["PostToolUse"] = [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/post_tool_use.py"}],
            },
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/lora_review_hook.py"}],
            },
        ]

    if hooks_config.get("notification", True):
        hooks["Notification"] = [{
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/notification.py"}],
        }]

    if hooks_config.get("subagent_stop", True):
        hooks["SubagentStop"] = [{
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/subagent_stop.py"}],
        }]

    # Stop hook: before the agent ends its response, poll the Maiko
    # inbox; if there are unread messages, block the stop and feed the
    # messages back so the agent picks them up automatically. Removes
    # the "agent forgot to call check_inbox" failure mode entirely.
    if hooks_config.get("stop", True):
        hooks["Stop"] = [{
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/stop.py"}],
        }]

    if not hooks:
        return

    # Auto-approve every server we wrote into .mcp.json so the
    # session doesn't stall on a trust prompt (we ran headless —
    # no human to click). Use the explicit `enabledMcpjsonServers`
    # allowlist instead of `enableAllProjectMcpServers: true`; the
    # blanket-true flag has a known Claude Code hang bug post-Feb
    # 2026 in non-interactive mode, and the allowlist also reads
    # less alarming on the security side.
    enabled_servers = []
    mcp_path = os.path.join(working_path, ".mcp.json")
    if os.path.isfile(mcp_path):
        try:
            with open(mcp_path, encoding="utf-8") as f:
                mcp_data = json.load(f)
            enabled_servers = list((mcp_data.get("mcpServers") or {}).keys())
        except Exception as e:
            logger.warning(f"[agent] Couldn't read .mcp.json for {task_id}: {e}")

    settings = {
        "hooks": hooks,
        "enabledMcpjsonServers": enabled_servers or ["maiko-channel"],
    }

    # Write .claude/settings.json
    claude_dir = os.path.join(working_path, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    with open(os.path.join(claude_dir, "settings.json"), "w") as f:
        json.dump(settings, f, indent=2)

    # Write .maiko-env.json for hook scripts to read
    from planet_maiko.config import maiko_api_url
    env_data = {
        "task_id": task_id,
        "agent_id": agent_id,
        "api_url": maiko_api_url(),
    }
    with open(os.path.join(working_path, ".maiko-env.json"), "w") as f:
        json.dump(env_data, f, indent=2)

    logger.info(f"[agent] Wrote Claude hooks settings for {agent_id} ({len(hooks)} hooks)")


# Branch names: git already forbids most metacharacters but defense in depth.