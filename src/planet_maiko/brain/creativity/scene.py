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


_creative_note_cache = {"text": None, "expires": 0}


def _generate_creative_note(weather, season, time_bucket, mood):
    """Generate a one-sentence atmospheric description via LLM."""
    import time
    now = time.time()
    if _creative_note_cache["text"] and now < _creative_note_cache["expires"]:
        return _creative_note_cache["text"]

    try:
        from planet_maiko.agents.brain_session import _get_runtime
        runtime = _get_runtime()
        if not runtime or not runtime.is_available():
            return None

        from planet_maiko.agents.routing import resolve_model
        prompt = (
            f"Write a single atmospheric sentence (max 20 words) describing this scene: "
            f"{weather} weather, {season}, {time_bucket}, mood: {mood}. "
            f"Be poetic and cozy, like a Studio Ghibli film narrator."
        )
        result = runtime.send(prompt, timeout=15, model=resolve_model("scene"))
        if result and result.get("success") and result.get("output"):
            text = result["output"].strip().strip('"')
            _creative_note_cache["text"] = text
            _creative_note_cache["expires"] = now + 3600  # 1 hour cache
            return text
    except Exception:
        pass
    return None


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
        now = datetime.now(timezone.utc)

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
