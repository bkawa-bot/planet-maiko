"""XDG-compliant directory resolution for Planet Maiko.

Config: ~/.config/planet-maiko/config.yaml  (override: MAIKO_CONFIG)
Data:   ~/.local/share/planet-maiko/maiko.db (override: MAIKO_DB_PATH)
"""

import os
import sys


def _config_home():
    if env := os.environ.get("MAIKO_CONFIG_DIR"):
        return env
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), ".config")
    return os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config"))


def _data_home():
    if env := os.environ.get("MAIKO_DATA_DIR"):
        return env
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.environ.get("XDG_DATA_HOME", os.path.join(os.path.expanduser("~"), ".local", "share"))


def config_dir():
    return os.path.join(_config_home(), "planet-maiko")


def data_dir():
    return os.path.join(_data_home(), "planet-maiko")


def config_path():
    return os.environ.get("MAIKO_CONFIG", os.path.join(config_dir(), "config.yaml"))


def db_path():
    return os.environ.get("MAIKO_DB_PATH", os.path.join(data_dir(), "maiko.db"))


def static_dir():
    """Path to the bundled frontend static files."""
    return os.path.join(os.path.dirname(__file__), "static")


def ensure_dirs():
    """Create config and data directories if they don't exist."""
    os.makedirs(config_dir(), exist_ok=True)
    os.makedirs(data_dir(), exist_ok=True)
