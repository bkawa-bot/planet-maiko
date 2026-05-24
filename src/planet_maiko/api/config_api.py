from flask import Blueprint, jsonify, request
from planet_maiko.config import load_config, save_config

config_bp = Blueprint("config", __name__)


@config_bp.route("/config", methods=["GET"])
def get_config():
    """Get the current configuration."""
    config = load_config()
    # Redact sensitive fields for the frontend
    safe = {}
    for key, section in config.items():
        if isinstance(section, dict):
            safe[key] = {
                k: ("***" if k in ("api_key", "token") and v else v)
                for k, v in section.items()
            }
        else:
            safe[key] = section
    return jsonify(safe)


@config_bp.route("/config", methods=["PUT"])
def update_config():
    """Update configuration."""
    data = request.get_json()
    config = load_config()

    for key, section in data.items():
        if isinstance(section, dict) and key in config:
            for k, v in section.items():
                # Don't overwrite secrets with the redacted "***"
                if v == "***":
                    continue
                config[key][k] = v
        else:
            config[key] = section

    save_config(config)
    return jsonify({"status": "ok"})


def _test_integration_payload(integration):
    """Connectivity check for one integration. Returns (payload, status).

    The single source of truth shared by the /config/test route and the
    builtin plugins' `test_connection` setup actions, so there's one
    implementation of "is this integration reachable" rather than two.
    """
    import subprocess

    if integration == "github":
        try:
            result = subprocess.run(
                ["gh", "api", "user", "--jq", ".login"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return {"status": "ok", "user": result.stdout.strip()}, 200
            return {"status": "error", "message": result.stderr[:200]}, 400
        except FileNotFoundError:
            return {"status": "error", "message": "gh CLI not installed"}, 400

    if integration == "linear":
        config = load_config()
        api_key = config.get("linear", {}).get("api_key", "")
        if not api_key:
            return {"status": "error", "message": "No API key configured"}, 400
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://api.linear.app/graphql",
                data=b'{"query": "{ viewer { name } }"}',
                headers={"Authorization": api_key, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                import json
                data = json.loads(resp.read())
                name = data.get("data", {}).get("viewer", {}).get("name", "unknown")
                return {"status": "ok", "user": name}, 200
        except Exception as e:
            return {"status": "error", "message": str(e)}, 400

    if integration == "pagerduty":
        from planet_maiko.plugins.clients.pagerduty_client import PagerDutyClient
        try:
            client = PagerDutyClient()
        except ValueError as e:
            return {"status": "error", "message": str(e)}, 400
        try:
            me = client.fetch_me()
            name = me.get("name") or me.get("email") or "unknown"
            return {"status": "ok", "user": name}, 200
        except Exception as e:
            return {"status": "error", "message": str(e)}, 400

    return {"status": "error", "message": f"Unknown integration: {integration}"}, 400


@config_bp.route("/config/test/<integration>", methods=["POST"])
def test_integration(integration):
    """Test if an integration is working."""
    payload, status = _test_integration_payload(integration)
    return jsonify(payload), status


@config_bp.route("/config/linear/teams", methods=["GET"])
def linear_teams():
    """List the user's Linear teams so the Settings UI can offer a picker."""
    from planet_maiko.plugins.builtin.linear import LinearPlugin

    try:
        teams = LinearPlugin.fetch_teams()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Linear teams fetch failed: {e}"}), 502
    return jsonify({"teams": teams})


@config_bp.route("/config/linear/team-meta", methods=["GET"])
def linear_team_meta():
    """Fetch states / labels / cycles / projects / members for a team.

    Used by the Send-to-Linear modal to populate its pickers. Accepts
    ?team_id= for ad-hoc lookups (e.g. a team-switch picker); falls
    back to config.linear.team_id for the default case.
    """
    from planet_maiko.plugins.clients.linear_client import LinearClient

    team_id = request.args.get("team_id")
    if not team_id:
        team_id = (load_config().get("linear") or {}).get("team_id") or ""
    if not team_id:
        return jsonify({"error": "team_id required (config or query param)"}), 400

    try:
        client = LinearClient()
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        meta = client.team_meta(team_id)
    except Exception as e:
        return jsonify({"error": f"team-meta fetch failed: {e}"}), 502
    return jsonify(meta)


def _gh_discover_payload(username):
    """Discover repos the user recently pushed to via the gh CLI.

    Returns (payload, status). Shared by the /github/discover route
    (setup wizard) and the github plugin's `discover_repos` setup
    action, so the gh preflight + fallback logic lives in one place.
    """
    import subprocess
    import json as _json
    import shutil as _shutil

    if not username:
        return {"error": "GitHub username not configured"}, 400

    # Explicit preflight so the setup wizard can show a clean next step
    # instead of a generic "discovery failed" when the real issue is a
    # missing binary or a logged-out session.
    if not _shutil.which("gh"):
        return {
            "error": "gh CLI not found",
            "hint": "Install it from https://cli.github.com, then re-run",
            "action": "install",
        }, 400
    try:
        # Scope to github.com so users with extra hosts (GHE, gitea) don't
        # fail this preflight just because one of the extras is logged out.
        auth_check = subprocess.run(
            ["gh", "auth", "status", "-h", "github.com"], capture_output=True, text=True, timeout=5,
        )
        if auth_check.returncode != 0:
            return {
                "error": "gh CLI isn't authenticated",
                "hint": "Run: gh auth login",
                "action": "auth",
            }, 400
    except subprocess.TimeoutExpired:
        return {"error": "gh auth status timed out"}, 500
    except FileNotFoundError:
        return {
            "error": "gh CLI not found",
            "hint": "Install it from https://cli.github.com",
            "action": "install",
        }, 400

    try:
        # Get repos the user recently pushed to (last 60 days). Computed
        # relative to today so this doesn't silently degrade to "no
        # results" once the hardcoded date drifts past relevance.
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
        result = subprocess.run(
            ["gh", "api", f"/search/commits?q=author:{username}+committer-date:>{cutoff}&sort=committer-date&per_page=30"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            # Fallback: list repos the user has push access to
            result = subprocess.run(
                ["gh", "repo", "list", username, "--limit", "20", "--json", "nameWithOwner,pushedAt",
                 "--jq", ".[].nameWithOwner"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return {"error": f"gh CLI failed: {result.stderr[:200]}"}, 500
            repos = [r.strip() for r in result.stdout.strip().split("\n") if r.strip()]
            return {"repos": repos[:10], "source": "repo_list"}, 200

        data = _json.loads(result.stdout)
        # Extract unique repo names from commit search
        seen = set()
        repos = []
        for item in data.get("items", []):
            repo_name = item.get("repository", {}).get("full_name", "")
            if repo_name and repo_name not in seen:
                seen.add(repo_name)
                repos.append(repo_name)

        return {"repos": repos[:10], "source": "recent_commits"}, 200

    except FileNotFoundError:
        return {"error": "gh CLI not found. Install it and run 'gh auth login'"}, 500
    except subprocess.TimeoutExpired:
        return {"error": "gh CLI timed out"}, 500
    except Exception as e:
        return {"error": str(e)}, 500


@config_bp.route("/github/discover", methods=["POST"])
def discover_github_repos():
    """Discover recent repos the user has committed to via gh CLI."""
    username = load_config().get("github", {}).get("username", "")
    payload, status = _gh_discover_payload(username)
    return jsonify(payload), status


@config_bp.route("/pollers/status", methods=["GET"])
def poller_status():
    """Get status of all poller plugins."""
    from datetime import datetime as _dt, timezone as _tz
    from planet_maiko.plugins.loader import get_plugins
    from planet_maiko.plugins.poller import PollerPlugin

    out = {}
    for p in get_plugins():
        if not isinstance(p, PollerPlugin):
            continue
        cfg = load_config().get(p.config_key or p.name, {}) or {}
        # PollerPlugin sets `_last_polled` (Unix seconds) on every poll;
        # surface as ISO so the Settings strip can render "last ran X ago".
        last = getattr(p, "_last_polled", 0) or 0
        last_iso = (
            _dt.fromtimestamp(last, _tz.utc).isoformat() if last > 0 else None
        )
        out[p.name] = {
            "type": "poller",
            "enabled": bool(cfg.get("enabled")),
            "running": True,
            "interval_minutes": cfg.get("poll_interval_minutes", 5),
            "last_run_at": last_iso,
        }
    return jsonify(out)


@config_bp.route("/pollers/<name>/run", methods=["POST"])
def run_poller(name):
    """Manually trigger a specific poller plugin."""
    from flask import current_app
    from planet_maiko.plugins.loader import get_plugins
    from planet_maiko.plugins.poller import PollerPlugin

    plugin = next(
        (p for p in get_plugins()
         if isinstance(p, PollerPlugin) and p.name == name),
        None,
    )
    if plugin is None:
        return jsonify({"error": f"Unknown poller: {name}"}), 404
    try:
        plugin.force_poll(current_app._get_current_object())
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
