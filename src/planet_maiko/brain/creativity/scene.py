"""Creativity engine - generates dynamic pixel art scene descriptors.

Pure rules engine (no LLM needed) that combines:
    - Time of day (dawn, morning, afternoon, dusk, night)
    - Weather (from NWS API or config)
    - Season (from date + hemisphere)
    - Holidays (configurable date ranges)
    - Moon phase (29-day cycle)

Outputs a scene descriptor that the frontend renders as pixel art.
Optional LLM tinting pass can add creative color variations.
"""

import logging
import math
import os
from datetime import datetime, timezone, date

logger = logging.getLogger(__name__)

# Time buckets
def _time_bucket(hour):
    if 5 <= hour < 7: return "dawn"
    if 7 <= hour < 12: return "morning"
    if 12 <= hour < 17: return "afternoon"
    if 17 <= hour < 20: return "dusk"
    return "night"

# Season from month (Northern hemisphere default)
def _season(month, southern=False):
    seasons = {
        (12, 1, 2): "winter",
        (3, 4, 5): "spring",
        (6, 7, 8): "summer",
        (9, 10, 11): "autumn",
    }
    for months, s in seasons.items():
        if month in months:
            if southern:
                flip = {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}
                return flip[s]
            return s
    return "spring"

# Moon phase (29.53 day cycle)
def _moon_phase(d):
    known_new = date(2024, 1, 11)
    days = (d - known_new).days
    cycle = days % 29.53
    if cycle < 1.85: return "new"
    if cycle < 7.38: return "waxing_crescent"
    if cycle < 9.23: return "first_quarter"
    if cycle < 14.77: return "waxing_gibbous"
    if cycle < 16.61: return "full"
    if cycle < 22.15: return "waning_gibbous"
    if cycle < 23.99: return "last_quarter"
    return "waning_crescent"

# Holiday detection
HOLIDAYS = {
    "halloween": ((10, 25), (10, 31)),
    "christmas": ((12, 15), (12, 31)),
    "valentine": ((2, 10), (2, 14)),
    "newyear": ((1, 1), (1, 3)),
    "lunar_newyear": ((1, 22), (2, 10)),
    "st_patricks": ((3, 14), (3, 17)),
    "pride": ((6, 1), (6, 30)),
    "independence_day": ((7, 1), (7, 4)),
    "day_of_dead": ((10, 31), (11, 2)),
    "thanksgiving": ((11, 22), (11, 28)),
}

def _detect_holiday(d):
    for name, ((sm, sd), (em, ed)) in HOLIDAYS.items():
        start = date(d.year, sm, sd)
        end = date(d.year, em, ed)
        if start <= d <= end:
            return name
    return None

# Sky types
SKY_MAP = {
    ("clear", "dawn"): "dawn",
    ("clear", "morning"): "clear_day",
    ("clear", "afternoon"): "clear_day",
    ("clear", "dusk"): "dusk",
    ("clear", "night"): "night_clear",
    ("cloudy", "night"): "night_cloudy",
    ("cloudy", "dawn"): "overcast",
    ("cloudy", "morning"): "overcast",
    ("cloudy", "afternoon"): "overcast",
    ("cloudy", "dusk"): "dusk",
    ("rain", "night"): "night_cloudy",
    ("rain", "dawn"): "stormy",
    ("rain", "morning"): "stormy",
    ("rain", "afternoon"): "stormy",
    ("rain", "dusk"): "stormy",
    ("snow", "night"): "snow_night",
    ("snow", "dawn"): "snow_day",
    ("snow", "morning"): "snow_day",
    ("snow", "afternoon"): "snow_day",
    ("snow", "dusk"): "snow_day",
    ("fog", "morning"): "fog",
    ("fog", "night"): "fog",
}

# Hill palettes by season
HILL_PALETTES = {
    "spring": {"far": "#5a8a5a", "mid": "#4a7a4a", "near": "#3a6a3a"},
    "summer": {"far": "#4a7a2a", "mid": "#3a6a1a", "near": "#2a5a0a"},
    "autumn": {"far": "#8a6a3a", "mid": "#7a5a2a", "near": "#6a4a1a"},
    "winter": {"far": "#6a7a8a", "mid": "#5a6a7a", "near": "#4a5a6a"},
    "winter_snow": {"far": "#c0c8d0", "mid": "#b0b8c0", "near": "#a0a8b0"},
}

# Maiko outfits
OUTFIT_MAP = {
    "halloween": "witch_hat",
    "christmas": "santa_hat",
    "newyear": "party_hat",
    "valentine": "bow_tie",
    "lunar_newyear": "red_envelope",
    "st_patricks": "clover_hat",
    "pride": "rainbow_scarf",
    "independence_day": "party_hat",
    "day_of_dead": "flower_crown",
    "thanksgiving": "scarf",
}

WEATHER_OUTFITS = {
    "rain": "umbrella",
    "snow": "scarf",
    "fog": "scarf",
}

SEASON_OUTFITS = {
    "spring": "flower_crown",
    "summer": "sunglasses",
    "autumn": "leaf_crown",
    "winter": "scarf",
}

# Special decorative elements
HOLIDAY_SPECIALS = {
    "halloween": ["pumpkins", "bats", "ghosts"],
    "christmas": ["christmas_tree", "lights", "snowman"],
    "valentine": ["hearts"],
    "newyear": ["fireworks"],
    "lunar_newyear": ["lanterns", "dragon", "fireworks"],
    "st_patricks": ["shamrocks", "rainbow"],
    "pride": ["rainbow_flags", "confetti"],
    "independence_day": ["fireworks", "flags"],
    "day_of_dead": ["marigolds", "candles", "skulls"],
    "thanksgiving": ["cornucopia", "autumn_leaves"],
}

SEASON_SPECIALS = {
    "spring": ["flowers_spring", "butterflies"],
    "summer": ["fireflies"],
    "autumn": ["falling_leaves"],
    "winter": ["aurora"],
}


import threading

# In-memory cache for the LLM-generated atmospheric note.
#   text       — last successful sentence (or None if we've never gotten one)
#   expires    — when to refresh on the next request
#   refreshing — guards against multiple background refreshes piling up
#   next_retry — negative cache: when the last attempt failed, don't try
#                again until this timestamp (avoids hammering Claude on
#                every /api/scene poll if the runtime is down)
# `text` and `expires` are persisted to <data_dir>/scene_note.json so a
# `maiko serve` restart inherits the still-valid sentence instead of
# paying for a fresh LLM generation every reboot. `refreshing` and
# `next_retry` are runtime-only (a paused refresh from one process
# shouldn't lock out the next one).
_creative_note_cache = {
    "text": None,
    "expires": 0,
    "refreshing": False,
    "next_retry": 0,
}


def _scene_note_cache_path():
    from planet_maiko.paths import data_dir
    return os.path.join(data_dir(), "scene_note.json")


def _load_persisted_cache():
    """Restore (text, expires) from disk on module import.

    Best-effort — a missing/corrupt file just means the next /api/scene
    call kicks off a fresh refresh, the same as a true cold start.
    """
    import json as _json
    try:
        path = _scene_note_cache_path()
        if not os.path.isfile(path):
            return
        with open(path, encoding="utf-8") as f:
            data = _json.load(f)
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            _creative_note_cache["text"] = data["text"]
            _creative_note_cache["expires"] = float(data.get("expires") or 0)
    except Exception as e:
        logger.debug(f"[scene] cache load failed: {e}")


def _persist_cache():
    """Write the cache to disk after a successful refresh."""
    import json as _json
    try:
        path = _scene_note_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            _json.dump({
                "text": _creative_note_cache["text"],
                "expires": _creative_note_cache["expires"],
            }, f)
    except Exception as e:
        logger.debug(f"[scene] cache persist failed: {e}")


_load_persisted_cache()


def _refresh_creative_note(weather, season, time_bucket, mood):
    """Background worker that runs the LLM call and updates the cache.
    Runs in a daemon thread so /api/scene can return instantly."""
    import time
    try:
        from planet_maiko.agents.brain_session import _get_runtime
        # task_type="scene" routes through OllamaRuntime by default
        # (see DEFAULT_RUNTIME in agents/routing.py). Falls back to
        # the brain.runtime default automatically if Ollama isn't
        # running, so a missing local server just sends this back
        # through Claude same as before.
        runtime = _get_runtime("scene")
        if not runtime or not runtime.is_available():
            # Negative-cache for 5 min so we don't keep checking
            # availability on every poll.
            _creative_note_cache["next_retry"] = time.time() + 300
            return

        from planet_maiko.agents.routing import resolve_model, resolve_effort
        prompt = (
            f"Write a single atmospheric sentence (max 20 words) describing this scene: "
            f"{weather} weather, {season}, {time_bucket}, mood: {mood}. "
            f"Be poetic and cozy, like a Studio Ghibli film narrator."
        )
        # runtime.name lets resolve_model honor per-runtime model
        # overrides (routing.runtime_models.<runtime>.scene = "...").
        result = runtime.send(
            prompt, timeout=15,
            model=resolve_model("scene", runtime.name),
            effort=resolve_effort("scene"),
        )
        if result and result.get("success") and result.get("output"):
            text = result["output"].strip().strip('"')
            _creative_note_cache["text"] = text
            # 4h TTL, matched to the home overview's regen cadence
            # (DEFAULT_MAX_AGE_HOURS in brain/overview.py). Scene note
            # is decorative, not alerting, so syncing with the overview
            # means one LLM call per overview cycle instead of two.
            _creative_note_cache["expires"] = time.time() + 14400
            _creative_note_cache["next_retry"] = 0
            _persist_cache()
        else:
            # LLM responded but with no usable output — back off shorter
            _creative_note_cache["next_retry"] = time.time() + 300
    except Exception as e:
        logger.debug(f"[scene] creative note refresh failed: {e}")
        _creative_note_cache["next_retry"] = time.time() + 300
    finally:
        _creative_note_cache["refreshing"] = False


def _generate_creative_note(weather, season, time_bucket, mood):
    """Return the cached atmospheric sentence, kicking off a refresh
    in a background thread when the cache is empty / expired.

    Never blocks the caller — /api/scene is polled by every page in the
    UI, and a 15s LLM call inline would (and did) flood the logs with
    "Claude code timed out after 15s" every few seconds.
    """
    import time
    now = time.time()
    cached = _creative_note_cache["text"]

    # Fresh cache — return as-is.
    if cached and now < _creative_note_cache["expires"]:
        return cached

    # Cache stale or empty. Maybe schedule a refresh — but only one at a
    # time, and not while we're in negative-cache cooldown.
    if (
        not _creative_note_cache["refreshing"]
        and now >= _creative_note_cache["next_retry"]
    ):
        _creative_note_cache["refreshing"] = True
        threading.Thread(
            target=_refresh_creative_note,
            args=(weather, season, time_bucket, mood),
            daemon=True,
            name="scene-creative-note",
        ).start()

    # Return the previous value if we have one (slightly stale beats
    # blocking); otherwise None and the UI will fall back to the
    # season poem.
    return cached


def generate(weather="clear", temperature_f=70, latitude=37.7, now=None):
    """Generate a scene descriptor.

    Args:
        weather: clear, cloudy, rain, snow, fog, thunderstorm
        temperature_f: temperature in Fahrenheit
        latitude: for hemisphere detection
        now: datetime override (for testing)

    Returns:
        dict with full scene descriptor
    """
    if now is None:
        # Scene time-of-day (dawn/morning/dusk/night) has to line up with
        # the user's wall clock — a UTC hour silently makes the pixel
        # art say "afternoon" at 7am Pacific.
        from planet_maiko.config import user_now
        now = user_now()

    today = now.date() if isinstance(now, datetime) else now
    hour = now.hour if isinstance(now, datetime) else 12

    time_bucket = _time_bucket(hour)
    season = _season(today.month, southern=(latitude < 0))
    holiday = _detect_holiday(today)
    moon = _moon_phase(today)

    # Normalize weather
    if weather == "thunderstorm":
        weather = "rain"
    if weather == "partly_cloudy":
        weather = "clear"

    # Sky
    sky = SKY_MAP.get((weather, time_bucket), "clear_day")
    if season == "winter" and sky == "clear_day":
        sky = "clear_day_winter"

    # Hills
    hill_key = season
    if weather == "snow" or (season == "winter" and temperature_f < 35):
        hill_key = "winter_snow"
    hills = HILL_PALETTES.get(hill_key, HILL_PALETTES["spring"])

    # Celestial
    if time_bucket == "night":
        celestial = {"type": "moon", "phase": moon}
    elif time_bucket in ("dawn", "dusk"):
        celestial = {"type": "sun", "variant": time_bucket}
    else:
        celestial = {"type": "sun", "variant": "bright" if weather == "clear" else "dim"}

    # Weather overlay
    weather_overlay = None
    if weather == "rain":
        weather_overlay = {"type": "rain", "intensity": "heavy" if temperature_f > 50 else "light"}
    elif weather == "snow":
        weather_overlay = {"type": "snow", "intensity": "heavy" if temperature_f < 25 else "light"}
    elif weather == "fog":
        weather_overlay = {"type": "fog", "intensity": "thick"}
    elif weather == "cloudy":
        weather_overlay = {"type": "clouds", "intensity": "overcast"}

    # Specials (decorative elements)
    specials = []
    if holiday and holiday in HOLIDAY_SPECIALS:
        specials.extend(HOLIDAY_SPECIALS[holiday])
    elif season in SEASON_SPECIALS:
        for s in SEASON_SPECIALS[season]:
            # Aurora only at night + clear + winter
            if s == "aurora" and (time_bucket != "night" or weather != "clear"):
                continue
            # Fireflies only at night
            if s == "fireflies" and time_bucket not in ("dusk", "night"):
                continue
            specials.append(s)

    # Maiko outfit
    if holiday and holiday in OUTFIT_MAP:
        outfit = OUTFIT_MAP[holiday]
    elif weather in WEATHER_OUTFITS:
        outfit = WEATHER_OUTFITS[weather]
    elif time_bucket == "night":
        outfit = "sleeping"
    elif season in SEASON_OUTFITS:
        outfit = SEASON_OUTFITS[season]
    else:
        outfit = "default"

    # Mood
    mood = f"{weather} {season} {time_bucket}"

    # Creative note via LLM tinting pass
    creative_note = _generate_creative_note(weather, season, time_bucket, mood)

    return {
        "generated_at": now.isoformat() if isinstance(now, datetime) else str(now),
        "context": {
            "weather": weather,
            "temperature_f": temperature_f,
            "season": season,
            "time_bucket": time_bucket,
            "holiday": holiday,
            "moon_phase": moon,
        },
        "scene": {
            "sky": sky,
            "hills": hills,
            "celestial": celestial,
            "weather_overlay": weather_overlay,
            "specials": specials,
            "maiko_outfit": outfit,
            "mood": mood,
            "creative_note": creative_note,
        },
    }
