import os
import yaml

from planet_maiko.paths import config_path as _config_path

CONFIG_PATH = _config_path()

# Default port for the Maiko API server.
# Override with MAIKO_PORT env var, or pass --port to `maiko serve`.
MAIKO_PORT = int(os.environ.get("MAIKO_PORT", "8420"))


def maiko_api_url():
    """Return the local Maiko API base URL.

    Honors MAIKO_API env var override (full URL), otherwise builds one
    from MAIKO_PORT. Used by the CLI, agent runtime injection, etc.
    """
    return os.environ.get("MAIKO_API", f"http://localhost:{MAIKO_PORT}/api")

DEFAULT_CONFIG = {
    "user": {
        "name": "",  # Your name (Maiko addresses you by this)
        # Hour (0-23) in local time when your workday wraps. Maiko uses
        # this to show a warm "enough for today" closing card in the
        # Home overview — appearing ~30 min before this hour and
        # lingering ~2h after. Anti-burnout plumbing: every other AI
        # product is engineered to keep you pulling the lever; Maiko
        # has an explicit closing condition. Set to null to disable.
        "workday_end_hour": 17,
        # Weekend mode — when true, Maiko assumes you're off-duty and
        # agents defer anything non-critical to the next workday. Not a
        # notification-muter: a durable, visible state that agents read
        # from their TASK.md preamble and the Home overview reflects in
        # its voice. Flip it on Friday, flip it off Monday.
        "weekend_mode": False,
        # Soft cap on "interruptions" Maiko is willing to surface per
        # local day. An interruption = a high or urgent pupdate actively
        # pulling you away from what you're doing. Once the count exceeds
        # the budget, the overview voice shifts toward batching (still
        # surfaces things, but framed as "a lot piled up — want to do
        # these together?"). Set to null to disable the signal entirely.
        "interruption_budget": 3,
        # IANA timezone name (e.g. "America/Los_Angeles"). Leave blank to
        # use the system's local timezone. Controls what "today" means for
        # the morning brief, skill-injected current_date, scene time-of-day,
        # and every other "when is it for Brigitte" check.
        "timezone": "",
    },
    "github": {
        "enabled": True,
        "username": "",
        "repos": [],  # e.g. ["org/repo1", "org/repo2"]
        "repo_roots": [],  # e.g. ["~/src", "~/projects"] — where your repos live on disk
        "poll_interval_minutes": 5,
    },
    "linear": {
        "enabled": False,
        "api_key": "",
        "team_id": "",
        "poll_interval_minutes": 60,
    },
    "calendar": {
        "enabled": False,
        "ical_urls": [],
        "poll_interval_minutes": 15,
    },
    "slack": {
        "enabled": False,
        "token": "",
        "channels": [],
        "poll_interval_minutes": 120,
    },
    "agents": {
        "custom_instructions": "",  # Legacy: appended to every coding agent's CLAUDE.md (still honored)
        "branch_prefix": "maiko",   # Prefix for auto-generated branch names (e.g. maiko/fix-auth-bug)
        # Team-wide instructions per agent role. Concatenated into the
        # prompt for every agent of that role, after the built-in
        # protocol and before the per-agent instructions. Empty by
        # default — users customize via Settings > Agents.
        "role_instructions": {
            "coding": "",
            "review": "",
            "investigation": "",
        },
    },
    "brain": {
        "runtime": "claude-code",  # or a custom runtime
        "llm_triage": True,  # use LLM for unmatched pupdates
        # Max LLM triage calls per processor pass. The processor caches
        # decisions by pupdate.type within a single pass, so this cap
        # is unusual to hit — a burst of 50 same-type pupdates costs
        # 1 LLM call, not 50. Raise if you're seeing `llm_deferred` in
        # cycle logs regularly.
        "llm_triage_per_cycle": 10,
        # Tools pre-approved for Claude Code sessions (avoids permission prompts)
        "allowed_tools": ["Bash", "Read", "Edit", "Write", "Glob", "Grep", "mcp__maiko-channel"],
        "correlation_window_minutes": 30,
        "incident_chains": [
            ["pr_ci_failed", "deploy_rollback", "error_spike"],
            ["deploy_blocked", "deploy_stuck"],
            ["pr_ci_failed", "deploy_blocked"],
            ["deploy_rollback", "error_spike"],
            ["batch_job_failing", "error_spike"],
            ["deploy_rollback", "batch_job_failing"],
        ],
        # When the correlator fires an incident, Maiko auto-creates an
        # investigation task. `dry_run` keeps the secondary safety on:
        # task appears in your list with extra.auto_spawned=true, but no
        # agent kickoff yet — flip dry_run off when you trust the
        # trigger to not surprise you. `enabled=false` turns the whole
        # feature off (no task, no agent). `daily_budget` hard-stops
        # runaway loops when upstream signals spike.
        "auto_investigate": {
            "enabled": True,
            "dry_run": False,
            "daily_budget": 5,
        },
        # Propagates ENABLE_PROMPT_CACHING_1H=1 to Claude Code subprocesses.
        # Anthropic shipped the 1-hour cache TTL on Apr 14 2026 — default
        # cache is 5m. Agent sessions here typically span 5-60 minutes and
        # re-read the same preamble (role protocol + TASK.md + skill
        # prompt), so 1h caching usually wins on cost after the second
        # read. Leave False if you're on a metered plan and want to
        # measure before committing.
        "prompt_cache_1h": False,
    },
    "scene": {
        "latitude": None,   # e.g. 37.77 for San Francisco
        "longitude": None,  # e.g. -122.42 for San Francisco
        # Visual ambient effects. Some folks find the drifting clouds /
        # rain and the hill backdrop too busy — these let them turn it
        # off for a plain background without losing the rest of the UI.
        "show_weather_overlay": True,
        "show_hill_background": True,
    },
    "overview": {
        # Optional user-provided add-on instruction appended to the
        # home-overview skill prompt. The LLM tries to honor this when
        # generating the rolling daily overview. Examples:
        #   "please also search my Slack for overnight mentions"
        #   "remind me which PRs have been sitting for more than 48 hours"
        # The skill runs as a full Claude Code agent with tool access,
        # so it can actually go do whatever the prompt asks.
        "custom_prompt": "",
    },
    "routing": {
        "enabled": True,
        "default_model": "sonnet",
        # low | medium | high | max — passed as `claude --effort <budget>`
        # to every LLM call: triage, clustering, skill runs, and the
        # autonomous coding / review / investigation agents. Max costs
        # more tokens but noticeably better for agent work.
        "thinking_budget": "medium",
    },
    "hooks": {
        "enabled": True,
        "post_tool_use": True,
        "post_compact": True,
        "notification": True,
        "subagent_stop": True,
    },
    "lora": {
        # Map a GitHub-style "org/repo" to a trained adapter path. Agents
        # scoped to that repo — review + coding — use this model via the
        # lora_check MCP tool. Absent repos skip LoRA checks gracefully.
        # Example:
        #   "bkawa-bot/planet-maiko": "~/.local/share/planet-maiko/models/planet-maiko-v3"
        "models_by_repo": {},
    },
    "setup_complete": False,
}


def load_config():
    """Load config from YAML file, falling back to defaults."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            user_config = yaml.safe_load(f) or {}
        # Merge user config over defaults
        config = {}
        for key, defaults in DEFAULT_CONFIG.items():
            if isinstance(defaults, dict):
                config[key] = {**defaults, **user_config.get(key, {})}
            else:
                config[key] = user_config.get(key, defaults)
        # Preserve any extra top-level keys the user added
        for key in user_config:
            if key not in config:
                config[key] = user_config[key]
        return config
    return dict(DEFAULT_CONFIG)


def save_config(config):
    """Write config back to YAML file."""
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def get_integration_config(name):
    """Get config for a specific integration."""
    config = load_config()
    return config.get(name, {})


def user_tz():
    """Return a tzinfo for the user.

    Prefers the IANA zone in user.timezone (e.g. "America/Los_Angeles"),
    falls back to the system's local tz. Returned object is always safe
    to pass to datetime.now() / astimezone().
    """
    from datetime import datetime
    tz_name = (load_config().get("user", {}) or {}).get("timezone", "")
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz_name)
        except Exception:
            # Bad zone name — fall through to local. Don't crash the app
            # on a typo'd config value; the rest of the system still works.
            pass
    return datetime.now().astimezone().tzinfo


def user_now():
    """Current time in the user's timezone. Always tz-aware."""
    from datetime import datetime
    return datetime.now(user_tz())
