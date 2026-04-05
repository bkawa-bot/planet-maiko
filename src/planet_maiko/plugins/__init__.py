"""Planet Maiko plugin system.

Plugins extend Planet Maiko without modifying the core. Two ways to add plugins:

1. Local: drop a .py file in ~/.maiko/plugins/
2. Package: pip install a package with a planet_maiko.plugins entry_point

See base.py for the MaikoPlugin interface.
"""

from planet_maiko.plugins.base import MaikoPlugin
from planet_maiko.plugins.loader import get_plugins, fire_hook

__all__ = ["MaikoPlugin", "get_plugins", "fire_hook"]
