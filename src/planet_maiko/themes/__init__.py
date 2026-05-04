"""User-created custom themes.

Themes are stored as JSON files in data_dir()/themes/<id>.json, with one
file per theme. Each file maps a small whitelist of CSS variable names
(stored in snake_case) to color strings, plus a bit of meta (id, name,
emoji, world_background).

The frontend fetches them from /api/themes, injects a <style> tag with
the CSS var overrides when one is selected, and flips data-theme on the
document to "custom:<id>".

The implementation is wrapped in a `ThemeStore` class so tests can
spin up a temporary directory (`ThemeStore("/tmp/test-themes")`)
without monkey-patching paths. Module-level functions delegate to a
lazy-initialized singleton — every existing call site
(`list_themes()`, `get_theme(id)`, etc.) keeps working unchanged.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

from planet_maiko.paths import data_dir

logger = logging.getLogger(__name__)


# The set of CSS variables a theme is allowed to set. Snake_case in JSON
# maps to kebab-case in CSS (bg_card → --bg-card). Anything else is
# rejected at save time so we don't accidentally grant arbitrary CSS
# injection through user-authored themes.
ALLOWED_COLOR_KEYS = {
    # Backgrounds. bg_plain is the flat page backdrop visible when the
    # world_background is "none" (hills off) — it also sits behind the
    # hill SVG when one is on, so an intentional miscoloring here shows
    # through where the SVG is transparent. Falls back to bg when unset.
    "bg", "bg_plain", "bg_card", "bg_card_alt", "bg_hover", "bg_selected",
    # Text
    "text", "text_dim", "text_muted",
    # Accent hues
    "pink", "pink_soft", "pink_glow",
    "blue", "blue_soft",
    "mint", "orange", "lavender", "peach", "creamsicle", "lemon",
    # Priority / status
    "urgent", "high", "normal", "low", "green",
    # Borders
    "border", "border_subtle",
    # Themed tint backgrounds
    "urgent_soft", "urgent_faint",
    "high_soft", "high_faint",
    "lemon_soft", "lemon_faint",
    "green_soft", "green_faint",
    "lavender_soft", "lavender_faint",
    # Surface backgrounds — drive the .topbar gradient and the
    # .overview-pane / .home-widget frosted background. `topbar_gradient`
    # takes a full CSS linear-gradient() string; `pane_bg` takes an
    # rgba() value so the frosted blur keeps its transparency.
    "topbar_gradient", "pane_bg",
    # Four individual color stops for the topbar gradient, surfaced as
    # regular color pickers in the theme designer. When any are set the
    # frontend composes a 135deg linear-gradient from them — overrides
    # topbar_gradient. Naming is spatial (left→right along the bar):
    # top_left at 0%, middle_left at 33%, middle_right at 66%, top_right
    # at 100%. Empty fields are dropped from the composed gradient.
    "topbar_stop_top_left",
    "topbar_stop_middle_left",
    "topbar_stop_middle_right",
    "topbar_stop_top_right",
}

# Hill SVGs the user can pick for the body background. "none" means a
# plain background color — matches the show_hill_background toggle.
ALLOWED_WORLD_BACKGROUNDS = {"none", "night", "day", "morning", "sunset"}

# A tolerant but bounded color regex: hex (3/4/6/8 digits), rgb/rgba/hsl/hsla
# function notation, a bounded linear-gradient() for the topbar, or a few
# named colors that commonly show up in palettes.
#
# The linear-gradient branch excludes `;{}()` inside the parens to prevent
# CSS injection via nested rules or function calls, and caps the body at
# 400 chars to prevent runaway. Color stops must be hex (no rgba inside
# the gradient) because allowing nested parens would defeat the guard.
_COLOR_RE = re.compile(
    r"^("
    r"#[0-9a-fA-F]{3,8}"
    r"|rgba?\(\s*\d+\s*(,\s*\d+\s*){2}(,\s*[0-9.]+\s*)?\)"
    r"|hsla?\(\s*\d+(\s*,\s*[0-9.]+%?){2,3}(\s*,\s*[0-9.]+)?\s*\)"
    r"|linear-gradient\([^;{}()]{1,400}\)"
    r"|transparent|currentColor|inherit"
    r")$"
)

# Theme IDs become CSS attribute values and filenames — restrict accordingly.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")


def validate_theme(data):
    """Return (cleaned_theme_dict, None) or (None, error_message).

    Does not touch the filesystem — useful for previews too. Static
    helper since validation has no per-store state.
    """
    if not isinstance(data, dict):
        return None, "theme must be a JSON object"

    theme_id = (data.get("id") or "").strip()
    if not _ID_RE.match(theme_id):
        return None, "id must be lowercase letters, digits, _ or -, 1-49 chars, starting with alphanumeric"

    name = (data.get("name") or "").strip()
    if not name or len(name) > 80:
        return None, "name is required and must be ≤80 chars"

    colors = data.get("colors") or {}
    if not isinstance(colors, dict):
        return None, "colors must be an object"

    unknown = [k for k in colors if k not in ALLOWED_COLOR_KEYS]
    if unknown:
        return None, f"unknown color keys: {', '.join(sorted(unknown)[:5])}"

    cleaned_colors = {}
    for key, value in colors.items():
        if not isinstance(value, str) or not _COLOR_RE.match(value.strip()):
            return None, f"invalid color for '{key}'"
        cleaned_colors[key] = value.strip()

    # We require at least the core surface colors so picking the theme
    # doesn't produce an unreadable half-applied look.
    required = {"bg", "text"}
    missing = required - cleaned_colors.keys()
    if missing:
        return None, f"missing required color(s): {', '.join(sorted(missing))}"

    world_bg = data.get("world_background", "none")
    if world_bg not in ALLOWED_WORLD_BACKGROUNDS:
        return None, f"world_background must be one of {sorted(ALLOWED_WORLD_BACKGROUNDS)}"

    emoji = (data.get("emoji") or "🎨").strip()
    if len(emoji) > 8:
        return None, "emoji too long"

    cleaned = {
        "id": theme_id,
        "name": name,
        "emoji": emoji,
        "colors": cleaned_colors,
        "world_background": world_bg,
        "description": (data.get("description") or "").strip()[:280],
        "created_at": data.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return cleaned, None


class ThemeStore:
    """Theme persistence over a directory of JSON files.

    Each theme is one file: <directory>/<id>.json. The store handles
    listing, reading, writing, and deletion; validation is delegated
    to the module-level `validate_theme` (it has no per-store state).

    Pass `directory=None` to use the default `data_dir()/themes`. Pass
    a real path to scope the store to a temp dir in tests.
    """

    def __init__(self, directory=None):
        self._directory = directory  # None → resolve lazily via data_dir()

    @property
    def directory(self):
        if self._directory is not None:
            return self._directory
        return os.path.join(data_dir(), "themes")

    def _ensure_dir(self):
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, theme_id):
        return os.path.join(self.directory, f"{theme_id}.json")

    def list(self):
        """Return every saved theme as a list of dicts, sorted by name."""
        self._ensure_dir()
        out = []
        for fname in os.listdir(self.directory):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.directory, fname), encoding="utf-8") as f:
                    data = json.load(f)
                # Filename is authoritative if id got out of sync somehow.
                data["id"] = fname[:-5]
                out.append(data)
            except Exception as e:
                logger.warning(f"[themes] Skipping unreadable theme {fname}: {e}")
        out.sort(key=lambda t: t.get("name", "").lower())
        return out

    def get(self, theme_id):
        """Return the theme by id, or None if missing / id is invalid."""
        if not _ID_RE.match(theme_id or ""):
            return None
        path = self._path(theme_id)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["id"] = theme_id
        return data

    def save(self, data):
        """Validate and write a theme. Returns (theme, None) or (None, error)."""
        cleaned, err = validate_theme(data)
        if err:
            return None, err
        self._ensure_dir()
        with open(self._path(cleaned["id"]), "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2)
        return cleaned, None

    def delete(self, theme_id):
        """Remove a theme file. True if removed, False if missing / invalid id."""
        if not _ID_RE.match(theme_id or ""):
            return False
        path = self._path(theme_id)
        if not os.path.isfile(path):
            return False
        os.remove(path)
        return True


# ---------------------------------------------------------------------------
# Module-level facade — every existing call site routes through a lazy
# singleton so tests that need an isolated dir can do
#    ThemeStore("/tmp/test").list()
# without affecting the global store. New code should prefer the class.
# ---------------------------------------------------------------------------

_default_store = None


def _store():
    global _default_store
    if _default_store is None:
        _default_store = ThemeStore()
    return _default_store


def themes_dir():
    return _store().directory


def list_themes():
    return _store().list()


def get_theme(theme_id):
    return _store().get(theme_id)


def save_theme(data):
    return _store().save(data)


def delete_theme(theme_id):
    return _store().delete(theme_id)
