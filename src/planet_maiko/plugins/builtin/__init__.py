"""Built-in plugins that ship with Maiko.

Each .py file in this directory is loaded at startup the same way
external plugins are; the loader walks the package and instantiates
every MaikoPlugin subclass it finds.

A built-in plugin can be turned off from Settings just like a
user-installed one. Disable by name (the plugin's `name` attribute).
"""
