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


@config_bp.route("/config/test/<integration>", methods=["POST"])
def test_integration(integration):
    """Test if an integration is working."""
    import subprocess

    if integration == "github":
        try:
            result = subprocess.run(
                ["gh", "api", "user", "--jq", ".login"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return jsonify({"status": "ok", "user": result.stdout.strip()})
            return jsonify({"status": "error", "message": result.stderr[:200]}), 400
        except FileNotFoundError:
            return jsonify({"status": "error", "message": "gh CLI not installed"}), 400

    elif integration == "linear":
        config = load_config()
        api_key = config.get("linear", {}).get("api_key", "")
        if not api_key:
            return jsonify({"status": "error", "message": "No API key configured"}), 400
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
                return jsonify({"status": "ok", "user": name})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 400

    return jsonify({"status": "error", "message": f"Unknown integration: {integration}"}), 400


@config_bp.route("/config/linear/teams", methods=["GET"])
def linear_teams():
    """List the user's Linear teams so the Settings UI can offer a picker."""
    from planet_maiko.pollers.linear_poller import LinearPoller

    try:
        teams = LinearPoller.fetch_teams()
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
    from planet_maiko.pollers.linear_client import LinearClient

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


@config_bp.route("/github/discover", methods=["POST"])
def discover_github_repos():
    """Discover recent repos the user has committed to via gh CLI."""
    import subprocess
    import json as _json
    import shutil as _shutil

    config = load_config()
    username = config.get("github", {}).get("username", "")

    if not username:
        return jsonify({"error": "GitHub username not configured"}), 400

    # Explicit preflight so the setup wizard can show a clean next step
    # instead of a generic "discovery failed" when the real issue is a
    # missing binary or a logged-out session.
    if not _shutil.which("gh"):
        return jsonify({
            "error": "gh CLI not found",
            "hint": "Install it from https://cli.github.com, then re-run",
            "action": "install",
        }), 400
    try:
        auth_check = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=5,
        )
        if auth_check.returncode != 0:
            return jsonify({
                "error": "gh CLI isn't authenticated",
                "hint": "Run: gh auth login",
                "action": "auth",
            }), 400
    except subprocess.TimeoutExpired:
        return jsonify({"error": "gh auth status timed out"}), 500
    except FileNotFoundError:
        return jsonify({
            "error": "gh CLI not found",
            "hint": "Install it from https://cli.github.com",
            "action": "install",
        }), 400

    try:
        # Get repos the user recently pushed to (last 30 days)
        result = subprocess.run(
            ["gh", "api", f"/search/commits?q=author:{username}+committer-date:>2026-03-01&sort=committer-date&per_page=30"],
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
                return jsonify({"error": f"gh CLI failed: {result.stderr[:200]}"}), 500
            repos = [r.strip() for r in result.stdout.strip().split("\n") if r.strip()]
            return jsonify({"repos": repos[:10], "source": "repo_list"})

        data = _json.loads(result.stdout)
        # Extract unique repo names from commit search
        seen = set()
        repos = []
        for item in data.get("items", []):
            repo_name = item.get("repository", {}).get("full_name", "")
            if repo_name and repo_name not in seen:
                seen.add(repo_name)
                repos.append(repo_name)

        return jsonify({"repos": repos[:10], "source": "recent_commits"})

    except FileNotFoundError:
        return jsonify({"error": "gh CLI not found. Install it and run 'gh auth login'"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "gh CLI timed out"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@config_bp.route("/pollers/status", methods=["GET"])
def poller_status():
    """Get status of all pollers."""
    from flask import current_app
    scheduler = current_app.config.get("SCHEDULER")
    if scheduler:
        return jsonify(scheduler.get_status())
    return jsonify({})


@config_bp.route("/pollers/<name>/run", methods=["POST"])
def run_poller(name):
    """Manually trigger a specific poller."""
    from flask import current_app
    scheduler = current_app.config.get("SCHEDULER")
    if not scheduler:
        return jsonify({"error": "Scheduler not running"}), 503

    try:
        created = scheduler.run_once(name)
        return jsonify({"status": "ok", "created": created})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
