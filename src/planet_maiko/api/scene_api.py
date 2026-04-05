import logging
import time
import urllib.request
import urllib.error
import json

from flask import Blueprint, jsonify, request
from planet_maiko.brain.creativity.scene import generate
from planet_maiko.brain.suggestions.scanner import quick_scan
from planet_maiko.config import load_config

logger = logging.getLogger(__name__)

scene_bp = Blueprint("scene", __name__)


# ---------------------------------------------------------------------------
# Open-Meteo weather integration
# ---------------------------------------------------------------------------

# Simple in-memory cache: {"data": {...}, "fetched_at": epoch_seconds}
_weather_cache = {}
_CACHE_TTL_SECONDS = 15 * 60  # 15 minutes

# WMO weather code -> our weather type
# https://open-meteo.com/en/docs  (WMO Weather interpretation codes)
_WMO_MAP = {
    0: "clear",
    1: "clear",
    2: "cloudy",
    3: "cloudy",
    45: "fog",
    48: "fog",
    51: "rain",
    53: "rain",
    55: "rain",
    56: "rain",
    57: "rain",
    61: "rain",
    63: "rain",
    65: "rain",
    66: "rain",
    67: "rain",
    71: "snow",
    73: "snow",
    75: "snow",
    77: "snow",
    80: "rain",
    81: "rain",
    82: "rain",
    85: "snow",
    86: "snow",
    95: "rain",
    96: "rain",
    99: "rain",
}


def _fetch_weather(lat, lon):
    """Fetch current weather from Open-Meteo (free, no API key).

    Returns {"weather": str, "temperature_f": int} or None on failure.
    Caches results for 15 minutes.
    """
    cache_key = f"{lat},{lon}"

    # Check cache
    cached = _weather_cache.get(cache_key)
    if cached and (time.time() - cached["fetched_at"]) < _CACHE_TTL_SECONDS:
        return cached["data"]

    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,weather_code"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "PlanetMaiko/2.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read())

        current = payload.get("current", {})
        weather_code = current.get("weather_code", 0)
        temp_c = current.get("temperature_2m", 21.1)  # default ~70F

        weather_type = _WMO_MAP.get(weather_code, "clear")
        temperature_f = round(temp_c * 9 / 5 + 32)

        data = {"weather": weather_type, "temperature_f": temperature_f}
        _weather_cache[cache_key] = {"data": data, "fetched_at": time.time()}
        logger.info("Fetched weather from Open-Meteo: %s", data)
        return data

    except Exception:
        logger.warning("Failed to fetch weather from Open-Meteo, using defaults")
        return None


def _get_weather_for_scene():
    """Get weather data from Open-Meteo if location is configured, else defaults."""
    config = load_config()
    scene_cfg = config.get("scene", {})
    lat = scene_cfg.get("latitude")
    lon = scene_cfg.get("longitude")

    if lat is not None and lon is not None:
        result = _fetch_weather(lat, lon)
        if result:
            return result

    # Fallback: clear skies, 70F
    return {"weather": "clear", "temperature_f": 70}


# --- Scene ---

@scene_bp.route("/scene", methods=["GET"])
def get_scene():
    """Get current pixel art scene descriptor.

    If scene.latitude and scene.longitude are configured, fetches live
    weather from Open-Meteo.  Query params can still override.
    """
    # Start with live weather (or defaults)
    live = _get_weather_for_scene()

    # Allow query-param overrides
    weather = request.args.get("weather", live["weather"])
    temp = int(request.args.get("temperature_f", str(live["temperature_f"])))

    scene = generate(weather=weather, temperature_f=temp)
    return jsonify(scene)


@scene_bp.route("/scene/refresh", methods=["POST"])
def refresh_scene():
    """Clear the weather cache so next request fetches fresh data."""
    _weather_cache.clear()
    return jsonify({"status": "ok"})


# --- Suggestions ---

@scene_bp.route("/suggestions/scan", methods=["POST"])
def run_scan():
    """Run quick suggestion scan."""
    data = request.get_json(silent=True) or {}
    repos = data.get("repos", [])
    result = quick_scan(repos=repos)
    return jsonify(result)
