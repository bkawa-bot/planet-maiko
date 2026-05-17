"""Admin / server lifecycle CLI commands.

Commands: serve, status, backup, backup-list, restore.

These are the things that genuinely need a terminal. Setup and
bootstrap-from-PRs moved to the in-app SetupWizard and Knowledge
page; backup ops stay here because there's no DB-snapshot UI.
"""

import sys

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


def cmd_up(args):
    """One command to bring the whole thing up.

    `maiko up` is `maiko serve` plus the Vite frontend dev server plus
    opening the browser for you, so you don't babysit two terminals.
    Ctrl+C takes both down. If there's no `frontend/` (running from an
    installed package) or no npm, it falls back to serving the bundled
    UI straight from the backend.
    """
    import os
    import sys
    import time
    import signal
    import shutil
    import subprocess
    import webbrowser
    import urllib.request
    from pathlib import Path

    host = args.host
    backend_port = args.port
    web_port = args.web_port

    # src/planet_maiko/cli/admin_cmds.py -> repo root is parents[3].
    repo_root = Path(__file__).resolve().parents[3]
    frontend_dir = repo_root / "frontend"
    have_frontend = (frontend_dir / "package.json").exists()
    npm = shutil.which("npm")

    procs = []

    def _spawn(cmd, cwd=None):
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True  # own process group to kill cleanly
        p = subprocess.Popen(cmd, cwd=cwd, **kwargs)
        procs.append(p)
        return p

    def _shutdown():
        for p in procs:
            if p.poll() is not None:
                continue
            try:
                if os.name == "nt":
                    # /T kills the child tree (vite spawns esbuild/node).
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(p.pid)],
                        capture_output=True,
                    )
                else:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass
        for p in procs:
            try:
                p.wait(timeout=8)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    # Backend via the same interpreter so PATH doesn't matter.
    print(f"[up] backend  http://{host}:{backend_port}")
    _spawn(
        [sys.executable, "-m", "planet_maiko.cli.main",
         "serve", "--host", host, "--port", str(backend_port)],
        cwd=str(repo_root),
    )

    if have_frontend and npm:
        print(f"[up] frontend http://localhost:{web_port}  (vite)")
        _spawn([npm, "run", "dev"], cwd=str(frontend_dir))
        open_url = f"http://localhost:{web_port}"
    else:
        why = "no frontend/ dir" if not have_frontend else "npm not found"
        print(f"[up] {why}; serving the bundled UI from the backend")
        open_url = f"http://{host}:{backend_port}"

    try:
        if not args.no_open:
            deadline = time.time() + 90
            while time.time() < deadline:
                if any(p.poll() is not None for p in procs):
                    break  # something died early; don't open a dead URL
                try:
                    urllib.request.urlopen(open_url, timeout=1)
                    break
                except Exception:
                    time.sleep(1)
            if all(p.poll() is None for p in procs):
                print(f"[up] opening {open_url}")
                try:
                    webbrowser.open(open_url)
                except Exception:
                    pass

        while True:
            for p in procs:
                rc = p.poll()
                if rc is not None:
                    print(f"[up] a process exited (code {rc}); shutting down")
                    return
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[up] stopping...")
    finally:
        _shutdown()


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


def cmd_inspect_prompt(args):
    """Print what get_skill_prompt actually returns for a given skill,
    plus the DB row state so you can tell whether a default skill is
    being served from file or from a (stale) DB override.

    Most useful for diagnosing "I edited the protocol but agents
    aren't seeing it" — if user_edited=True on a default skill, the
    DB prompt wins over the file. The reset-skill command clears it.
    """
    from planet_maiko.app import create_app
    from planet_maiko.database import db
    from planet_maiko.models.custom_skill import CustomSkill
    from planet_maiko.agents.skills import get_skill_prompt

    skill_id = args.skill_id
    app = create_app(start_scheduler=False)
    with app.app_context():
        row = db.session.get(CustomSkill, skill_id)
        if row is None:
            print(f"No CustomSkill row for {skill_id!r}.")
        else:
            print(f"DB row for {skill_id!r}:")
            print(f"  name:         {row.name}")
            print(f"  is_default:   {row.is_default}")
            print(f"  user_edited:  {row.user_edited}")
            print(f"  prompt chars: {len(row.prompt or '')}")

        prompt = get_skill_prompt(skill_id, {
            "task_title": "<title>",
            "task_id": "task-inspect",
            "maiko_port": "8420",
            "agent_identity": "<agent>",
            "agent_signature": "",
        })
        if prompt is None:
            print("\nget_skill_prompt returned None.")
            return
        print(f"\nLive get_skill_prompt -> {len(prompt)} chars.")
        # Heuristic: real protocols are >=1000 chars; if much shorter,
        # the agent is seeing the placeholder.
        if len(prompt) < 500:
            print("(Suspiciously short - likely the placeholder. "
                  "Try `maiko reset-skill <id>` to drop user_edited.)")
        print("\nFirst 800 chars:\n")
        # Encode safely for Windows cp1252 consoles — agent prompts
        # contain unicode (emoji, em-dashes) that crashes cp1252's
        # charmap. Replace unencodable chars rather than raise.
        encoding = (sys.stdout.encoding or "utf-8")
        sys.stdout.write(prompt[:800].encode(encoding, errors="replace").decode(encoding))
        print()


def cmd_reset_skill(args):
    """Clear user_edited on a default skill so the next read pulls
    fresh content from the prompt file. Idempotent."""
    from planet_maiko.app import create_app
    from planet_maiko.database import db
    from planet_maiko.models.custom_skill import CustomSkill

    app = create_app(start_scheduler=False)
    with app.app_context():
        row = db.session.get(CustomSkill, args.skill_id)
        if row is None:
            print(f"No CustomSkill row for {args.skill_id!r}.")
            return
        if not row.is_default:
            print(f"{args.skill_id!r} isn't a default skill — refusing to "
                  f"reset (you'd lose the user-authored prompt).")
            return
        if not row.user_edited:
            print(f"{args.skill_id!r} already at default (user_edited=False). "
                  f"Next agent will read from prompts/{args.skill_id}.md.")
            return
        row.user_edited = False
        db.session.commit()
        print(f"Reset user_edited on {args.skill_id!r}. The file at "
              f"prompts/{args.skill_id}.md is now authoritative.")


def cmd_db_schema(args):
    """Print every table's columns + flag missing patches.

    Diagnostic for the "I added a column but don't see it in my DB"
    case — checks the live schema against _PATCH_COLUMNS and reports
    which patches are present, missing, or already applied.
    """
    from planet_maiko.app import create_app, _PATCH_COLUMNS
    from planet_maiko.database import db
    from sqlalchemy import text

    app = create_app(start_scheduler=False)
    with app.app_context():
        with db.engine.begin() as conn:
            tables = [
                r[0] for r in conn.execute(text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                )).all()
            ]
            target = (args.table or "").strip().lower() or None
            for t in tables:
                if target and t.lower() != target:
                    continue
                rows = conn.execute(text(f"PRAGMA table_info({t})")).all()
                print(f"\n{t} ({len(rows)} columns)")
                for r in rows:
                    # PRAGMA: cid, name, type, notnull, default, pk
                    nullness = "NOT NULL" if r[3] else ""
                    pk = " PK" if r[5] else ""
                    print(f"  {r[1]:30s} {r[2]:20s}{nullness}{pk}")

            print("\nPatch column status:")
            for table, column, col_type in _PATCH_COLUMNS:
                rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
                names = {r[1] for r in rows}
                if not names:
                    state = "table missing"
                elif column in names:
                    state = "present"
                else:
                    state = "MISSING — boot will patch"
                print(f"  {table}.{column} ({col_type}) — {state}")


def register(subparsers):
    """Register admin/server lifecycle subcommands."""
    # maiko status
    p = subparsers.add_parser("status", help="Check Planet Maiko status")
    p.set_defaults(func=cmd_status)

    # maiko db-schema [--table NAME]
    p = subparsers.add_parser("db-schema", help="Print live DB schema + patch column status")
    p.add_argument("--table", default=None, help="Only show this table")
    p.set_defaults(func=cmd_db_schema)

    # maiko inspect-prompt <skill_id>
    p = subparsers.add_parser("inspect-prompt", help="Show what an agent gets for a skill prompt (file vs DB override)")
    p.add_argument("skill_id", help="Skill id, e.g. agent-protocol or review-agent-protocol")
    p.set_defaults(func=cmd_inspect_prompt)

    # maiko reset-skill <skill_id>
    p = subparsers.add_parser("reset-skill", help="Clear user_edited on a default skill so file content is authoritative")
    p.add_argument("skill_id", help="Skill id, e.g. agent-protocol")
    p.set_defaults(func=cmd_reset_skill)

    # maiko serve
    p = subparsers.add_parser("serve", help="Start Planet Maiko server")
    p.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    p.add_argument("--port", type=int, default=MAIKO_PORT, help="Port to listen on")
    p.add_argument("--debug", action="store_true", help="Enable debug mode")
    p.set_defaults(func=cmd_serve)

    # maiko up : backend + frontend + open the browser, one command
    p = subparsers.add_parser(
        "up", help="Start backend + frontend and open the browser (one command)"
    )
    p.add_argument("--host", default="127.0.0.1", help="Backend host to bind to")
    p.add_argument("--port", type=int, default=MAIKO_PORT, help="Backend port")
    p.add_argument("--web-port", type=int, default=5173,
                   help="Vite dev server port (default 5173)")
    p.add_argument("--no-open", action="store_true",
                   help="Don't open the browser")
    p.set_defaults(func=cmd_up)

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
