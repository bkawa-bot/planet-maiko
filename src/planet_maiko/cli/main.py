#!/usr/bin/env python3
"""maiko CLI — communicate with Planet Maiko from anywhere.

This module is the entry point and argparse dispatcher. The actual
command implementations live in:

- cli/agent_cmds.py — agent communication (report, task, inbox, reply, ...)
- cli/admin_cmds.py — server lifecycle (status, setup, serve, desktop, ...)
- cli/lora_cmds.py  — LoRA training/eval/feedback (train, retrain, review, ...)
- cli/_helpers.py    — shared api_request + task_id detector

Usage:
    maiko report "Status message"
    maiko task done [task-id]
    maiko status
    maiko serve
"""

import argparse
import logging
import sys

from planet_maiko.cli import admin_cmds, agent_cmds, lora_cmds


def main():
    parser = argparse.ArgumentParser(
        prog="maiko",
        description="Planet Maiko - Personal engineering intelligence dashboard",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Register all subcommands from their respective modules
    agent_cmds.register(subparsers)
    admin_cmds.register(subparsers)
    lora_cmds.register(subparsers)

    # Let plugins register CLI commands
    try:
        from planet_maiko.plugins.loader import discover_plugins
        for plugin in discover_plugins():
            plugin.register_commands(subparsers)
    except Exception as e:
        logging.getLogger(__name__).debug(f"[cli] Plugin command registration skipped: {e}")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
