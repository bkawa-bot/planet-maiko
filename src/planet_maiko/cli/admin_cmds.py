"""Admin / server lifecycle CLI commands.

Commands: status, setup, serve, desktop, seed, bootstrap.
These mostly do local file/database work and don't need the API to be up.
"""

import json
import urllib.parse
import urllib.request

from planet_maiko.cli._helpers import api_request
from planet_maiko.config import MAIKO_PORT


def cmd_status(args):
    """Show system health status."""
    import subprocess

    print("=== Planet Maiko Status ===\n")

    # Check backend
    try:
        data = api_request("/brain/status")
        print(f"Backend: running on :{MAIKO_PORT}")
        print(f"  Brain cycles: {data.get('cycle_count', 0)}")
        last = data.get('last_cycle')
        print(f"  Last cycle: {last or 'never'}")
    except SystemExit:
        # api_request calls sys.exit on failure; catch it here to continue
        print("Backend: NOT running (start with: maiko serve)")

    # Check gh CLI
    try:
        result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=5)
        print(f"gh CLI: {'authenticated' if result.returncode == 0 else 'not authenticated'}")
    except FileNotFoundError:
        print("gh CLI: not installed")

    # Check config
    try:
        from planet_maiko.config import load_config
        config = load_config()
        username = config.get("github", {}).get("username", "")
        repos = config.get("github", {}).get("repos", [])
        location = config.get("scene", {}).get("location_name", "")
        print(f"Config: {'configured' if username else 'needs setup (run: maiko setup)'}")
        if username:
            print(f"  GitHub: {username} ({len(repos)} repo(s))")
        if location:
            print(f"  Location: {location}")
    except Exception:
        print("Config: error loading")

    # Check database
    try:
        from planet_maiko.paths import data_dir
        import os
        db_path = os.path.join(data_dir(), "maiko.db")
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
            print(f"Database: {db_path} ({size_mb:.1f} MB)")
        else:
            print(f"Database: not created yet (start server first)")
    except Exception:
        print("Database: error checking")

    print()


def cmd_setup(args):
    """Interactive first-time setup."""
    from planet_maiko.config import load_config, save_config
    import subprocess

    print("=== Planet Maiko Setup ===\n")

    config = load_config()

    # GitHub username
    current_user = config.get("github", {}).get("username", "")
    username = input(f"GitHub username [{current_user}]: ").strip() or current_user
    config.setdefault("github", {})["username"] = username
    config["github"]["enabled"] = True

    # Test gh CLI
    try:
        result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print("  gh CLI: authenticated")
        else:
            print("  gh CLI: not authenticated. Run 'gh auth login' first.")
    except FileNotFoundError:
        print("  gh CLI: not installed. Install from https://cli.github.com/")

    # Repos
    current_repos = config.get("github", {}).get("repos", [])
    repos_input = input(f"Repos (comma-separated) [{', '.join(current_repos)}]: ").strip()
    if repos_input:
        config["github"]["repos"] = [r.strip() for r in repos_input.split(",") if r.strip()]

    # Repo roots
    current_roots = config.get("github", {}).get("repo_roots", [])
    roots_input = input(f"Repo roots (local paths) [{', '.join(current_roots)}]: ").strip()
    if roots_input:
        config["github"]["repo_roots"] = [r.strip() for r in roots_input.split(",") if r.strip()]

    # Location
    location = input("City/zipcode for weather (or Enter to skip): ").strip()
    if location:
        try:
            url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(location)}&count=1&language=en&format=json"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
                if data.get("results"):
                    r = data["results"][0]
                    name = f"{r['name']}, {r.get('admin1', '')}"
                    config.setdefault("scene", {})["latitude"] = r["latitude"]
                    config["scene"]["longitude"] = r["longitude"]
                    config["scene"]["location_name"] = name
                    print(f"  Location: {name} ({r['latitude']}, {r['longitude']})")
        except Exception as e:
            print(f"  Could not resolve location: {e}")

    save_config(config)
    print(f"\nConfig saved. Start with: maiko serve")


def cmd_serve(args):
    """Start the Planet Maiko server."""
    from planet_maiko.app import create_app
    print(f"Starting Planet Maiko on http://{args.host}:{args.port}")
    app = create_app(start_scheduler=True)
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


def cmd_desktop(args):
    """Launch Planet Maiko as a desktop application."""
    from planet_maiko.desktop import main as desktop_main
    print(f"Launching Planet Maiko desktop on http://{args.host}:{args.port}")
    desktop_main(host=args.host, port=args.port)


def cmd_seed(args):
    """Populate the database with realistic test data."""
    from planet_maiko.app import create_app
    from planet_maiko.seed import seed_data
    app = create_app(start_scheduler=False)
    seed_data(app)
    print("Seed data loaded.")


def cmd_bootstrap(args):
    """Bootstrap learnings from past PR reviews."""
    from planet_maiko.app import create_app
    app = create_app(start_scheduler=False)
    with app.app_context():
        from planet_maiko.brain.learning.bootstrap import bootstrap_from_prs
        result = bootstrap_from_prs(limit=args.limit)
        print(f"\nCreated {result['total_created']} signals total.\n")
        for r in result["per_repo"]:
            if r["error"]:
                print(f"  {r['repo']:50s}  ERROR: {r['error']}")
            else:
                print(f"  {r['repo']:50s}  {r['prs_scanned']} PRs scanned, {r['signals_created']} signals")


def cmd_backup(args):
    """Take a one-off snapshot of the DB."""
    from planet_maiko.backups import create_backup
    result = create_backup("manual")
    if result.get("error"):
        print(f"Backup failed: {result['error']}")
        return
    print(f"Snapshot saved: {result['filename']} ({result['bytes'] // 1024} KB)")
    print(f"Path: {result['path']}")


def cmd_backup_list(args):
    """List all existing snapshots."""
    from planet_maiko.backups import list_backups
    bks = list_backups()
    if not bks:
        print("No backups yet. Run `maiko backup` to take one now.")
        return
    print(f"{len(bks)} snapshot(s):\n")
    for b in bks:
        size_kb = b["bytes"] // 1024
        print(f"  {b['filename']:40s}  {size_kb:>6} KB  {b['created_at']}")


def cmd_restore(args):
    """Restore a named snapshot over the live DB.

    Requires the server to be stopped — restoring while pollers are
    writing to the DB will corrupt it.
    """
    from planet_maiko.backups import restore_backup
    import sys
    prompt = (
        f"\nThis will overwrite the current database with snapshot '{args.filename}'.\n"
        f"The server must be stopped. Your current db will be copied aside first.\n"
        f"Type YES to continue: "
    )
    reply = input(prompt).strip()
    if reply != "YES":
        print("Aborted.")
        sys.exit(1)
    result = restore_backup(args.filename)
    if result.get("error"):
        print(f"Restore failed: {result['error']}")
        sys.exit(1)
    print(f"Restored from {result['restored']}.")
    print(f"Previous db stashed at: {result['previous_db']}")


def register(subparsers):
    """Register admin/server lifecycle subcommands."""
    # maiko setup
    p = subparsers.add_parser("setup", help="Interactive first-time setup")
    p.set_defaults(func=cmd_setup)

    # maiko status
    p = subparsers.add_parser("status", help="Check Planet Maiko status")
    p.set_defaults(func=cmd_status)

    # maiko serve
    p = subparsers.add_parser("serve", help="Start Planet Maiko server")
    p.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    p.add_argument("--port", type=int, default=MAIKO_PORT, help="Port to listen on")
    p.add_argument("--debug", action="store_true", help="Enable debug mode")
    p.set_defaults(func=cmd_serve)

    # maiko desktop
    p = subparsers.add_parser("desktop", help="Launch Planet Maiko as a desktop app")
    p.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    p.add_argument("--port", type=int, default=MAIKO_PORT, help="Port to listen on")
    p.set_defaults(func=cmd_desktop)

    # maiko seed
    p = subparsers.add_parser("seed", help="Populate database with test data")
    p.set_defaults(func=cmd_seed)

    # maiko bootstrap
    p = subparsers.add_parser("bootstrap", help="Seed learnings from past PR reviews")
    p.add_argument("--limit", type=int, default=20, help="Max PRs to scan")
    p.set_defaults(func=cmd_bootstrap)

    # maiko backup
    p = subparsers.add_parser("backup", help="Take a DB snapshot now")
    p.set_defaults(func=cmd_backup)

    # maiko backup-list (separate command since argparse subparsers
    # don't nest without extra scaffolding and this is fine for v1)
    p = subparsers.add_parser("backup-list", help="List existing DB snapshots")
    p.set_defaults(func=cmd_backup_list)

    # maiko restore
    p = subparsers.add_parser("restore", help="Restore a named snapshot (server must be stopped)")
    p.add_argument("filename", help="Snapshot filename as shown in `maiko backup-list`")
    p.set_defaults(func=cmd_restore)
