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
from importlib.metadata import entry_points
from pathlib import Path

logger = logging.getLogger(__name__)

_plugins = None


def _local_plugins_dir():
    """Get the local plugins directory (~/.maiko/plugins/)."""
    return Path.home() / ".maiko" / "plugins"


def _discover_from_entry_points():
    """Discover plugins registered via pip entry_points."""
    plugins = []
    eps = entry_points(group="planet_maiko.plugins")
    for ep in eps:
        try:
            plugin_cls = ep.load()
            plugin = plugin_cls()
            plugin._source = "entry_point"
            plugin._entry_point = ep.name
            plugins.append(plugin)
            logger.info(f"[plugins] Loaded entry_point plugin: {ep.name} ({plugin.name})")
        except Exception as e:
            logger.warning(f"[plugins] Failed to load entry_point '{ep.name}': {e}")
    return plugins


def _discover_from_local_dir():
    """Discover plugins from ~/.maiko/plugins/*.py files."""
    from planet_maiko.plugins.base import MaikoPlugin

    plugins = []
    plugins_dir = _local_plugins_dir()

    if not plugins_dir.is_dir():
        return plugins

    for path in sorted(plugins_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue

        module_name = f"maiko_local_plugin_{path.stem}"
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
                    plugins.append(plugin)
                    logger.info(f"[plugins] Loaded local plugin: {path.name} ({plugin.name})")

        except Exception as e:
            logger.warning(f"[plugins] Failed to load local plugin '{path.name}': {e}")

    return plugins


def discover_plugins():
    """Find all plugins from entry_points + local directory."""
    plugins = []
    plugins.extend(_discover_from_entry_points())
    plugins.extend(_discover_from_local_dir())

    # Deduplicate by name (entry_point takes precedence over local)
    seen = {}
    for p in plugins:
        if p.name in seen:
            logger.warning(f"[plugins] Duplicate plugin name '{p.name}', keeping first")
            continue
        seen[p.name] = p

    return list(seen.values())


def load_plugins(app):
    """Discover all plugins and call on_startup for each.

    Called once during create_app(). Also merges plugin config defaults.
    """
    global _plugins
    _plugins = discover_plugins()

    if not _plugins:
        logger.info("[plugins] No plugins found")
        return

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

    # Call on_startup
    for plugin in _plugins:
        try:
            plugin.on_startup(app)
        except Exception as e:
            logger.error(f"[plugins] on_startup failed for '{plugin.name}': {e}")

    # Register /api/plugins endpoint
    from flask import jsonify

    @app.route("/api/plugins", methods=["GET"])
    def list_plugins():
        return jsonify([
            {
                "name": p.name,
                "source": getattr(p, "_source", "unknown"),
                "file": getattr(p, "_file", None),
                "entry_point": getattr(p, "_entry_point", None),
                "status": "loaded",
            }
            for p in _plugins
        ])


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
