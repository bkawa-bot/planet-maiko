"""File-authoring helpers for the coding-agent worktree:
TASK.md, CLAUDE.md, .mcp.json, and the .claude/settings.local.json
hook config. All write into the worktree before kickoff."""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _write_task_file(working_path, job_id, job_title, prompt):
    """Write TASK.md so the agent knows what to do.

    The file name is TASK.md (the agent's "task" in the colloquial
    sense, the work in front of them); the underlying ID is the
    AgentJob.id.
    """
    # Human-readable file — show the user's local time, not UTC. Agents
    # and users both read this; a "Created: 23:30 UTC" line is confusing
    # when the user thinks of it as 3:30pm Pacific.
    from planet_maiko.config import user_now
    content = f"""# Task: {job_title}

**Job ID:** {job_id}
**Created:** {user_now().strftime('%Y-%m-%d %H:%M %Z')}

## Instructions

{prompt}
"""
    with open(os.path.join(working_path, "TASK.md"), "w", encoding="utf-8") as f:
        f.write(content)


def _write_claude_md(working_path, job_id, job_title, role="coding", maiko_port=None, parent_repo_path=None, agent_profile_id=None, specialty_id=None):
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

    # If role IS a CustomSkill with its own protocol_prompt set, the
    # skill is acting as a first-class agent type — use that as the
    # protocol template directly, skipping the role-default lookup.
    # Falls through to the normal path on any DB hiccup or when the
    # skill exists as a specialty (no protocol_prompt) rather than an
    # agent type. The same templating substitutions still apply below.
    custom_protocol_template = None
    try:
        from planet_maiko.models.custom_skill import CustomSkill
        from planet_maiko.database import db as _db
        cs = _db.session.get(CustomSkill, role)
        if cs is not None and cs.protocol_prompt:
            custom_protocol_template = cs.protocol_prompt
    except Exception:
        custom_protocol_template = None

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

    # Load protocol from skill prompt (editable via Skills page).
    # `task_title` / `task_id` and `job_title` / `job_id` template
    # vars both resolve to the same values, so user-customized
    # prompts work with either spelling.
    content = None
    if custom_protocol_template:
        # First-class custom agent type: render the user's
        # protocol_prompt verbatim with the same substitutions
        # get_skill_prompt would apply to a default protocol.
        content = custom_protocol_template
        for key, value in (
            ("job_title", job_title),
            ("job_id", job_id),
            ("task_title", job_title),
            ("task_id", job_id),
            ("maiko_port", str(maiko_port)),
            ("agent_identity", agent_identity),
            ("agent_signature", agent_signature),
        ):
            content = content.replace("{" + key + "}", str(value))
    if not content:
        try:
            from planet_maiko.agents.skills import get_skill_prompt
            content = get_skill_prompt(protocol_skill, {
                "job_title": job_title,
                "job_id": job_id,
                "task_title": job_title,
                "task_id": job_id,
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
            content = content.replace("{job_title}", job_title)
            content = content.replace("{job_id}", job_id)
            content = content.replace("{task_title}", job_title)
            content = content.replace("{task_id}", job_id)
            content = content.replace("{maiko_port}", str(maiko_port))
            content = content.replace("{agent_identity}", agent_identity)
            content = content.replace("{agent_signature}", agent_signature)
        except Exception:
            content = f"# Agent Protocol\n\nJob: {job_title}\nJob ID: {job_id}\nRole: {role}\n\nRead TASK.md for instructions."

    if role_instructions_for_role:
        content += f"\n\n## Team instructions for {role} agents\n\n{role_instructions_for_role.strip()}\n"

    # Character: who this agent is. Identity comes before operational
    # context so the rest of the prompt is read in the agent's own
    # voice. Without this section the agent has only its name (via the
    # template's {agent_identity}) and no archetype guidance for every
    # session past arrival.
    character_block = _build_character_section(agent_profile_id)
    if character_block:
        content += f"\n\n{character_block}\n"

    # Active Insights for this repo (and globals). Unlike Learnings,
    # Insights aren't confidence-gated or trainable. They're the
    # "things every new agent in this repo should know" playbook:
    # tooling quirks, in-flight migrations, team conventions that
    # aren't code rules. Insights tagged "overview" get promoted to
    # a Repo Overview block at the top; the rest render as the usual
    # Team Playbook bullets.
    #
    # context_tags scopes the injection: only insights whose tags
    # overlap the agent's job context surface (plus untagged + the
    # always-shown "overview" insights). Without this, every new
    # coding job inherited every Insight on the repo, including ones
    # that were tagged for unrelated projects / use-cases.
    context_tags = _build_context_tags(job_id, role, specialty_id)
    playbook = _build_playbook_section(parent_repo_path, context_tags=context_tags)
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


def _build_playbook_section(parent_repo_path, context_tags=None):
    """Render the Repo Overview + Team Playbook sections from active
    Insights scoped to this repo (or global).

    Thin wrapper around brain.learning.playbook.build_playbook — kept
    so existing call sites get the string form they expect. The
    underlying function also returns the structured insight list, which
    the read-surface HTTP endpoint uses.

    context_tags is forwarded to build_playbook so the agent only sees
    insights whose tags overlap its job context (plus untagged ones).
    """
    from planet_maiko.brain.learning.playbook import build_playbook
    return build_playbook(parent_repo_path, context_tags=context_tags)["playbook_md"]


def _build_context_tags(job_id, role, specialty_id):
    """Compose the agent's context tag set for insight scoping.

    Sources, in order:
      - role (e.g. "coding", "review", "investigation", "cartographer",
        or a CustomSkill id when the role IS a specialty).
      - job.kind — usually matches role but stays distinct for
        specialty-as-role runs where they can diverge.
      - specialty_id — the per-run specialty pick (None for most runs).
      - linked task's tags (Task.tags is a free-form list — the user
        often types things like "auth", "billing", "perf" there).
      - source pupdate's tags — covers automation-spawned jobs where
        no Task exists but the triggering pupdate has tags.
      - linked task's project title (lowercase, slug-friendly).

    Returns a set of lowercased strings. Best-effort: any lookup that
    fails contributes nothing rather than crashing prep.

    The set is intentionally broad — false positives (showing an
    insight that wasn't strictly relevant) are cheaper than false
    negatives (hiding an insight the agent needed). Users tune via
    tagging discipline: tag narrowly to scope tightly, leave untagged
    or tag broadly to keep an insight in the always-show set.
    """
    tags = set()
    if role:
        tags.add(str(role).lower())
    if specialty_id:
        tags.add(str(specialty_id).lower())

    try:
        from planet_maiko.models.agent_job import AgentJob
        from planet_maiko.models.task import Task
        from planet_maiko.models.project import Project
        from planet_maiko.models.pupdate import Pupdate
        from planet_maiko.database import db as _db

        job = _db.session.get(AgentJob, job_id) if job_id else None
        if job is not None:
            if job.kind:
                tags.add(str(job.kind).lower())
            task = (
                _db.session.get(Task, job.source_task_id)
                if job.source_task_id else None
            )
            if task is not None:
                for t in (task.tags or []):
                    if t:
                        tags.add(str(t).lower())
                if task.project_id:
                    project = _db.session.get(Project, task.project_id)
                    if project is not None and project.title:
                        tags.add(project.title.lower())
                # Source pupdate tags surface for automation-spawned
                # jobs whose triggering signal carries topic hints (e.g.
                # "ci", "deploy", "auth") even when the task itself is
                # generically titled.
                if task.source_pupdate_id:
                    pupdate = _db.session.get(Pupdate, task.source_pupdate_id)
                    if pupdate is not None:
                        for t in (pupdate.tags or []):
                            if t:
                                tags.add(str(t).lower())
    except Exception:
        # Defaulting to a smaller tag set just shrinks the candidate
        # matches; the safe-default (untagged insights pass) keeps the
        # agent informed even when this enrichment fails.
        pass

    return tags


def _build_character_section(agent_profile_id):
    """Render a "Your character" section so the agent has its full
    personality available during every session, not just at arrival.

    Pulls the agent's display_name, self-written tagline (flavor_text),
    and the archetype's bio_seed (rich guidance about voice and posture).
    Without this, the agent only knows its NAME from {agent_identity} in
    the protocol template; the archetype that gives it its voice was
    only used at arrival-time for bio-gen and never persisted into the
    working session. Result: status replies, PR comments, and chat tone
    all drift toward generic.

    Best-effort: missing profile or unrecognized card returns "".
    """
    if not agent_profile_id:
        return ""
    try:
        from planet_maiko.database import db
        from planet_maiko.models.agent_profile import AgentProfile
        from planet_maiko.agents.cards import get_card
        profile = db.session.get(AgentProfile, agent_profile_id)
        if not profile:
            return ""
        card = get_card(profile.avatar) if profile.avatar else None

        lines = [
            "## Your character",
            "",
            "This is who you are. Bring this voice to every reply, "
            "every PR comment, every status update. It is not optional "
            "flavor; it is the point of you.",
            "",
            f"**Your name:** {profile.display_name or 'unknown'}",
        ]
        if (profile.flavor_text or "").strip():
            lines.append(f"**Your tagline:** \"{profile.flavor_text.strip()}\"")
        if card:
            rarity = card.get("rarity", "common")
            lines.append(f"**Your archetype:** {card['display_name']} ({rarity})")
            if card.get("tagline"):
                lines.append(f"**Archetype tagline:** \"{card['tagline']}\"")
            if (card.get("bio_seed") or "").strip():
                lines.append("")
                lines.append("**Archetype guidance** (the vibe that shaped you):")
                lines.append("")
                lines.append(card["bio_seed"].strip())
        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"[claude_md] character section skipped: {e}")
        return ""


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


def _write_mcp_json(working_path, job_id, parent_repo_path=None):
    """Write .mcp.json carrying the user's parent-repo MCPs into the
    worktree (Linear / Slack / GitHub / etc.) so the agent inherits
    the same MCP surface area its parent repo had.

    maiko-channel is intentionally NOT added here anymore — that
    server's job (reply / inbox / check-code / leave-comment) is now
    covered by the `maiko` CLI, and its session-id reporting + Stop-
    hook polling are covered by the hook scripts in hooks/. If the
    user has no project-level MCPs configured, no file gets written.
    """
    import json

    # Only inherit; no maiko-channel layered on top.
    servers = _inherit_mcp_servers(parent_repo_path)
    if not servers:
        return

    mcp_config = {"mcpServers": servers}

    with open(os.path.join(working_path, ".mcp.json"), "w") as f:
        json.dump(mcp_config, f, indent=2)

    logger.info(
        f"[agent] Wrote .mcp.json with {len(servers)} inherited server(s) "
        f"({sorted(servers.keys())})"
    )


def _write_claude_settings(working_path, job_id, agent_id):
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

    # PostToolUse covers two things: git-commit/push event reporting (the
    # original purpose) AND inbox polling (the post-MCP replacement for
    # mid-flight push notifications — the agent sees new user messages
    # within one tool boundary instead of waiting for the next settle).
    # Matcher widened from "Bash" to "*" so inbox polling fires after
    # every tool call. The hook returns silently for tools that don't
    # need git reporting and don't have inbox messages, so the cost is
    # one HTTP GET per tool call (~50ms, comparable to the tool itself).
    if hooks_config.get("post_tool_use", True):
        hooks["PostToolUse"] = [
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/post_tool_use.py"}],
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

    # SessionStart: report CLAUDE_SESSION_ID to Maiko so the View
    # Session link in the UI can find the transcript on disk. This is
    # the last thing the maiko-channel MCP server did that the CLI +
    # other hooks weren't already covering; with this in place the
    # MCP path becomes optional.
    if hooks_config.get("session_start", True):
        hooks["SessionStart"] = [{
            "matcher": "*",
            "hooks": [{"type": "command", "command": f"python3 {hooks_dir}/session_start.py"}],
        }]

    if not hooks:
        return

    # Auto-approve every server we wrote into .mcp.json so the
    # session doesn't stall on a trust prompt (we ran headless —
    # no human to click). Use the explicit `enabledMcpjsonServers`
    # allowlist instead of `enableAllProjectMcpServers: true`; the
    # blanket-true flag has a known Claude Code hang bug post-Feb
    # 2026 in non-interactive mode, and the allowlist also reads
    # less alarming on the security side. If no .mcp.json was
    # written (no inherited servers, no maiko-channel anymore),
    # the allowlist is empty — that's fine, just no MCP at all.
    enabled_servers = []
    mcp_path = os.path.join(working_path, ".mcp.json")
    if os.path.isfile(mcp_path):
        try:
            with open(mcp_path, encoding="utf-8") as f:
                mcp_data = json.load(f)
            enabled_servers = list((mcp_data.get("mcpServers") or {}).keys())
        except Exception as e:
            logger.warning(f"[agent] Couldn't read .mcp.json for {job_id}: {e}")

    settings = {
        "hooks": hooks,
        "enabledMcpjsonServers": enabled_servers,
    }

    # Write .claude/settings.json
    claude_dir = os.path.join(working_path, ".claude")
    os.makedirs(claude_dir, exist_ok=True)
    with open(os.path.join(claude_dir, "settings.json"), "w") as f:
        json.dump(settings, f, indent=2)

    # Write .maiko-env.json for hook scripts to read. Hooks read
    # `job_id` first and fall back to the older `task_id` field, so
    # we only write the canonical name now.
    from planet_maiko.config import maiko_api_url
    env_data = {
        "job_id": job_id,
        "agent_id": agent_id,
        "api_url": maiko_api_url(),
    }
    with open(os.path.join(working_path, ".maiko-env.json"), "w") as f:
        json.dump(env_data, f, indent=2)

    logger.info(f"[agent] Wrote Claude hooks settings for {agent_id} ({len(hooks)} hooks)")


# Branch names: git already forbids most metacharacters but defense in depth.