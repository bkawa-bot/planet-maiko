"""Tests for the scene engine — time buckets, seasons, holidays, scene generation."""

import pytest
from datetime import datetime, date, timezone
from unittest.mock import patch
from planet_maiko.brain.creativity.scene import (
    generate,
    _time_bucket,
    _season,
    _detect_holiday,
    HOLIDAYS,
)


# ---------------------------------------------------------------------------
# _time_bucket
# ---------------------------------------------------------------------------


def test_time_bucket_dawn():
    assert _time_bucket(5) == "dawn"
    assert _time_bucket(6) == "dawn"


def test_time_bucket_morning():
    assert _time_bucket(7) == "morning"
    assert _time_bucket(11) == "morning"


def test_time_bucket_afternoon():
    assert _time_bucket(12) == "afternoon"
    assert _time_bucket(16) == "afternoon"


def test_time_bucket_dusk():
    assert _time_bucket(17) == "dusk"
    assert _time_bucket(19) == "dusk"


def test_time_bucket_night():
    assert _time_bucket(20) == "night"
    assert _time_bucket(23) == "night"
    assert _time_bucket(0) == "night"
    assert _time_bucket(4) == "night"


# ---------------------------------------------------------------------------
# _season
# ---------------------------------------------------------------------------


def test_season_winter():
    assert _season(12) == "winter"
    assert _season(1) == "winter"
    assert _season(2) == "winter"


def test_season_spring():
    assert _season(3) == "spring"
    assert _season(4) == "spring"
    assert _season(5) == "spring"


def test_season_summer():
    assert _season(6) == "summer"
    assert _season(7) == "summer"
    assert _season(8) == "summer"


def test_season_autumn():
    assert _season(9) == "autumn"
    assert _season(10) == "autumn"
    assert _season(11) == "autumn"


def test_season_southern_hemisphere_flips():
    assert _season(1, southern=True) == "summer"
    assert _season(7, southern=True) == "winter"
    assert _season(4, southern=True) == "autumn"
    assert _season(10, southern=True) == "spring"


# ---------------------------------------------------------------------------
# _detect_holiday
# ---------------------------------------------------------------------------


def test_detect_holiday_halloween():
    assert _detect_holiday(date(2026, 10, 31)) == "halloween"
    assert _detect_holiday(date(2026, 10, 25)) == "halloween"


def test_detect_holiday_christmas():
    assert _detect_holiday(date(2026, 12, 25)) == "christmas"
    assert _detect_holiday(date(2026, 12, 15)) == "christmas"


def test_detect_holiday_valentine():
    assert _detect_holiday(date(2026, 2, 14)) == "valentine"


def test_detect_holiday_newyear():
    assert _detect_holiday(date(2026, 1, 1)) == "newyear"
    assert _detect_holiday(date(2026, 1, 3)) == "newyear"


def test_detect_holiday_returns_none_for_non_holiday():
    # August 15 should not match any holiday
    assert _detect_holiday(date(2026, 8, 15)) is None


def test_detect_holiday_lunar_newyear():
    assert _detect_holiday(date(2026, 1, 29)) == "lunar_newyear"
    assert _detect_holiday(date(2026, 2, 5)) == "lunar_newyear"


def test_detect_holiday_pride():
    assert _detect_holiday(date(2026, 6, 1)) == "pride"
    assert _detect_holiday(date(2026, 6, 15)) == "pride"
    assert _detect_holiday(date(2026, 6, 30)) == "pride"


def test_detect_holiday_st_patricks():
    assert _detect_holiday(date(2026, 3, 17)) == "st_patricks"
    assert _detect_holiday(date(2026, 3, 14)) == "st_patricks"


def test_detect_holiday_independence_day():
    assert _detect_holiday(date(2026, 7, 4)) == "independence_day"


def test_detect_holiday_day_of_dead():
    assert _detect_holiday(date(2026, 11, 1)) == "day_of_dead"
    assert _detect_holiday(date(2026, 11, 2)) == "day_of_dead"


def test_detect_holiday_thanksgiving():
    assert _detect_holiday(date(2026, 11, 26)) == "thanksgiving"


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_returns_required_keys(mock_note):
    now = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
    scene = generate(weather="clear", temperature_f=75, now=now)

    assert "scene" in scene
    assert "context" in scene
    s = scene["scene"]
    assert "sky" in s
    assert "hills" in s
    assert "celestial" in s
    assert "weather_overlay" in s
    assert "specials" in s
    assert "maiko_outfit" in s
    assert "mood" in s


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_correct_time_bucket(mock_note):
    morning = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
    scene = generate(now=morning)
    assert scene["context"]["time_bucket"] == "morning"

    night = datetime(2026, 4, 10, 23, 0, tzinfo=timezone.utc)
    scene = generate(now=night)
    assert scene["context"]["time_bucket"] == "night"


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_correct_season(mock_note):
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    scene = generate(now=summer)
    assert scene["context"]["season"] == "summer"


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_holiday_specials(mock_note):
    halloween = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
    scene = generate(weather="clear", now=halloween)
    assert scene["context"]["holiday"] == "halloween"
    assert "pumpkins" in scene["scene"]["specials"]
    assert scene["scene"]["maiko_outfit"] == "witch_hat"


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_rain_weather_overlay(mock_note):
    now = datetime(2026, 5, 10, 14, 0, tzinfo=timezone.utc)
    scene = generate(weather="rain", temperature_f=60, now=now)
    overlay = scene["scene"]["weather_overlay"]
    assert overlay is not None
    assert overlay["type"] == "rain"


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_snow_weather_overlay(mock_note):
    now = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)
    scene = generate(weather="snow", temperature_f=20, now=now)
    overlay = scene["scene"]["weather_overlay"]
    assert overlay is not None
    assert overlay["type"] == "snow"
    assert overlay["intensity"] == "heavy"  # temp < 25


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_moon_at_night(mock_note):
    night = datetime(2026, 3, 15, 23, 0, tzinfo=timezone.utc)
    scene = generate(weather="clear", now=night)
    assert scene["scene"]["celestial"]["type"] == "moon"
    assert "phase" in scene["scene"]["celestial"]


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_sun_during_day(mock_note):
    day = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
    scene = generate(weather="clear", now=day)
    assert scene["scene"]["celestial"]["type"] == "sun"


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_southern_hemisphere_season_flip(mock_note):
    january = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    scene = generate(weather="clear", now=january, latitude=-33.8)
    assert scene["context"]["season"] == "summer"


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_sleeping_outfit_at_night(mock_note):
    # Non-holiday clear night
    night = datetime(2026, 8, 15, 23, 0, tzinfo=timezone.utc)
    scene = generate(weather="clear", now=night)
    assert scene["scene"]["maiko_outfit"] == "sleeping"


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_christmas_specials(mock_note):
    xmas = datetime(2026, 12, 25, 14, 0, tzinfo=timezone.utc)
    scene = generate(weather="snow", now=xmas)
    assert "christmas_tree" in scene["scene"]["specials"]
    assert scene["scene"]["maiko_outfit"] == "santa_hat"


@patch("planet_maiko.brain.creativity.scene._generate_creative_note", return_value=None)
def test_generate_pride_specials(mock_note):
    pride = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)
    scene = generate(weather="clear", now=pride)
    assert scene["context"]["holiday"] == "pride"
    assert "rainbow_flags" in scene["scene"]["specials"]
    assert scene["scene"]["maiko_outfit"] == "rainbow_scarf"
