import os
import yaml

from planet_maiko.paths import config_path as _config_path

CONFIG_PATH = _config_path()

DEFAULT_CONFIG = {
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
        "custom_instructions": "",  # Added to every agent's CLAUDE.md (your workflow preferences)
    },
    "brain": {
        "runtime": "claude-code",  # or a custom runtime
        "llm_triage": True,  # use LLM for unmatched pupdates
        # Tools to pre-approve for Claude Code sessions (avoids permission prompts)
        # e.g. ["Bash", "Read", "Edit", "Write", "WebFetch", "WebSearch", "mcp__github"]
        "allowed_tools": [],
        "correlation_window_minutes": 30,
        "incident_chains": [
            ["pr_ci_failed", "deploy_rollback", "error_spike"],
            ["deploy_blocked", "deploy_stuck"],
            ["pr_ci_failed", "deploy_blocked"],
            ["deploy_rollback", "error_spike"],
            ["batch_job_failing", "error_spike"],
            ["deploy_rollback", "batch_job_failing"],
        ],
    },
    "scene": {
        "latitude": None,   # e.g. 37.77 for San Francisco
        "longitude": None,  # e.g. -122.42 for San Francisco
    },
    "hooks": {
        "enabled": True,
        "post_tool_use": True,
        "post_compact": True,
        "notification": True,
        "subagent_stop": True,
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
