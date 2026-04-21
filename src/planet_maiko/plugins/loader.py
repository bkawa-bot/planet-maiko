"""Plugin discovery and loading.

Finds plugins from two sources:
1. Entry points (pip-installed packages): group "planet_maiko.plugins"
2. Local directory: ~/.maiko/plugins/*.py

Plugins are loaded once on startup. Use get_plugins() to access them
and fire_hook() to call a hook on all plugins.
"""

import importlib.util
import logging
import os
import sys
import traceback
from importlib.metadata import entry_points
from pathlib import Path

logger = logging.getLogger(__name__)

_plugins = None
# All discovered plugins (including disabled/errored) for the /api/plugins endpoint
_discovered = []


def _local_plugins_dir():
    """Get the local plugins directory (~/.maiko/plugins/)."""
    return Path.home() / ".maiko" / "plugins"


def _get_disabled_list():
    """Return the list of disabled plugin names from config."""
    try:
        from planet_maiko.config import load_config
        return load_config().get("plugins", {}).get("disabled", [])
    except Exception:
        return []


def _discover_from_entry_points(disabled):
    """Discover plugins registered via pip entry_points."""
    plugins = []
    results = []
    eps = entry_points(group="planet_maiko.plugins")
    for ep in eps:
        info = {
            "name": ep.name,
            "source": "entry_point",
            "entry_point": ep.name,
            "file": None,
            "status": "loaded",
            "error": None,
        }
        if ep.name in disabled:
            info["status"] = "disabled"
            results.append(info)
            logger.info(f"[plugins] Skipping disabled plugin: {ep.name}")
            continue
        try:
            plugin_cls = ep.load()
            plugin = plugin_cls()
            plugin._source = "entry_point"
            plugin._entry_point = ep.name
            info["name"] = plugin.name or ep.name
            plugins.append(plugin)
            logger.info(f"[plugins] Loaded entry_point plugin: {ep.name} ({plugin.name})")
        except Exception as e:
            info["status"] = "error"
            info["error"] = traceback.format_exc()
            logger.warning(f"[plugins] Failed to load entry_point '{ep.name}': {e}")
        results.append(info)
    return plugins, results


def _discover_from_local_dir(disabled):
    """Discover plugins from ~/.maiko/plugins/*.py files."""
    from planet_maiko.plugins.base import MaikoPlugin

    plugins = []
    results = []
    plugins_dir = _local_plugins_dir()

    if not plugins_dir.is_dir():
        return plugins, results

    for path in sorted(plugins_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue

        module_name = f"maiko_local_plugin_{path.stem}"
        info = {
            "name": path.stem,
            "source": "local",
            "entry_point": None,
            "file": str(path),
            "status": "loaded",
            "error": None,
        }

        if path.stem in disabled:
            info["status"] = "disabled"
            results.append(info)
            logger.info(f"[plugins] Skipping disabled plugin: {path.stem}")
            continue

        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Find all MaikoPlugin subclasses in the module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, MaikoPlugin)
                    and attr is not MaikoPlugin
                ):
                    plugin = attr()
                    plugin._source = "local"
                    plugin._file = str(path)
                    info["name"] = plugin.name or path.stem
                    plugins.append(plugin)
                    logger.info(f"[plugins] Loaded local plugin: {path.name} ({plugin.name})")

        except Exception as e:
            info["status"] = "error"
            info["error"] = traceback.format_exc()
            logger.warning(f"[plugins] Failed to load local plugin '{path.name}': {e}")

        results.append(info)

    return plugins, results


def discover_plugins():
    """Find all plugins from entry_points + local directory."""
    disabled = _get_disabled_list()
    ep_plugins, ep_results = _discover_from_entry_points(disabled)
    local_plugins, local_results = _discover_from_local_dir(disabled)

    plugins = ep_plugins + local_plugins
    all_results = ep_results + local_results

    # Deduplicate by name (entry_point takes precedence over local)
    seen = {}
    for p in plugins:
        if p.name in seen:
            logger.warning(f"[plugins] Duplicate plugin name '{p.name}', keeping first")
            continue
        seen[p.name] = p

    return list(seen.values()), all_results


def load_plugins(app):
    """Discover all plugins and call on_startup for each.

    Called once during create_app(). Also merges plugin config defaults.
    """
    global _plugins, _discovered
    _plugins, _discovered = discover_plugins()

    if not _plugins:
        logger.info("[plugins] No plugins found")
    else:
        logger.info(f"[plugins] Loading {len(_plugins)} plugin(s): {[p.name for p in _plugins]}")

    # Merge config defaults
    from planet_maiko.config import load_config, save_config
    config = load_config()
    config_changed = False
    for plugin in _plugins:
        try:
            defaults = plugin.get_config_defaults()
            if defaults:
                for key, value in defaults.items():
                    if key not in config:
                        config[key] = value
                        config_changed = True
        except Exception as e:
            logger.warning(f"[plugins] Config defaults failed for '{plugin.name}': {e}")
    if config_changed:
        save_config(config)

    # Capture per-plugin config schemas so /api/plugins can surface them
    # to Settings. Schema method is optional; plugins that skip it still
    # appear with just the on/off toggle (legacy behavior).
    for plugin in _plugins:
        try:
            schema = plugin.get_config_schema() or {}
        except Exception as e:
            logger.warning(f"[plugins] Config schema failed for '{plugin.name}': {e}")
            schema = {}
        for d in _discovered:
            if d["name"] == plugin.name:
                d["config_schema"] = schema
                # Store which top-level config key the plugin's values
                # live under. Convention: plugins declare defaults via
                # get_config_defaults() with a single top-level key
                # (their plugin name or similar); surface that here so
                # the frontend knows where to write user edits.
                defaults = {}
                try:
                    defaults = plugin.get_config_defaults() or {}
                except Exception:
                    pass
                d["config_key"] = next(iter(defaults.keys())) if defaults else plugin.name
                break

    # Call on_startup
    for plugin in _plugins:
        try:
            plugin.on_startup(app)
        except Exception as e:
            logger.error(f"[plugins] on_startup failed for '{plugin.name}': {e}")
            # Update discovered status to reflect startup failure
            for d in _discovered:
                if d["name"] == plugin.name:
                    d["error"] = str(e)

    # Register plugin API endpoints
    from flask import Blueprint, jsonify, request as flask_request

    plugins_bp = Blueprint("plugins", __name__)

    @plugins_bp.route("/plugins", methods=["GET"])
    def list_all_plugins():
        return jsonify(_discovered)

    @plugins_bp.route("/pupdate-types", methods=["GET"])
    def list_pupdate_types():
        """Pupdate types known to Maiko — built-ins + anything plugins
        registered via `register_pupdate_types()`. Drives the Automation
        editor's type dropdown.
        """
        from planet_maiko.pupdate_types import collect_all
        return jsonify(collect_all())

    @plugins_bp.route("/plugins/<name>/toggle", methods=["POST"])
    def toggle_plugin(name):
        """Enable or disable a plugin. Requires server restart to take effect."""
        config = load_config()
        disabled = config.get("plugins", {}).get("disabled", [])

        if name in disabled:
            disabled.remove(name)
            action = "enabled"
        else:
            disabled.append(name)
            action = "disabled"

        config.setdefault("plugins", {})["disabled"] = disabled
        save_config(config)

        # Update the in-memory discovered list so the UI reflects the change
        for d in _discovered:
            if d["name"] == name:
                d["status"] = "disabled" if action == "disabled" else "pending_restart"

        return jsonify({"status": action, "name": name, "restart_required": True})

    app.register_blueprint(plugins_bp, url_prefix="/api")


def get_plugins():
    """Return list of loaded plugins. Empty list if not yet loaded."""
    return _plugins or []


def fire_hook(hook_name, *args, **kwargs):
    """Call a hook on all loaded plugins, silently catching errors.

    Args:
        hook_name: method name on MaikoPlugin (e.g. "on_pupdate_created")
        *args, **kwargs: passed to the hook method
    """
    for plugin in get_plugins():
        method = getattr(plugin, hook_name, None)
        if method:
            try:
                method(*args, **kwargs)
            except Exception as e:
                logger.warning(f"[plugins] {hook_name} failed for '{plugin.name}': {e}")
