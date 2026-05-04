"""Admin / server lifecycle CLI commands.

Commands: serve, status, backup, backup-list, restore.

These are the things that genuinely need a terminal. Setup and
bootstrap-from-PRs moved to the in-app SetupWizard and Knowledge
page; backup ops stay here because there's no DB-snapshot UI.
"""

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
        print(f"Config: {'configured' if username else 'needs setup (open the dashboard)'}")
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


def cmd_serve(args):
    """Start the Planet Maiko server."""
    from planet_maiko.app import create_app
    print(f"Starting Planet Maiko on http://{args.host}:{args.port}")
    app = create_app(start_scheduler=True)
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


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
    # maiko status
    p = subparsers.add_parser("status", help="Check Planet Maiko status")
    p.set_defaults(func=cmd_status)

    # maiko serve
    p = subparsers.add_parser("serve", help="Start Planet Maiko server")
    p.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    p.add_argument("--port", type=int, default=MAIKO_PORT, help="Port to listen on")
    p.add_argument("--debug", action="store_true", help="Enable debug mode")
    p.set_defaults(func=cmd_serve)

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
