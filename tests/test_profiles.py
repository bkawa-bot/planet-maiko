"""Tests for agent profile management — names, avatars, initial stats."""

from planet_maiko.agents.profiles import create_profile, TECH_SUFFIXES


def test_create_profile_adds_tech_suffix(app, db):
    profile = create_profile("agent-1", display_name="Glitch")
    assert any(profile.display_name.endswith(suffix) for suffix in TECH_SUFFIXES)
    assert profile.display_name.startswith("Glitch")


def test_create_profile_assigns_avatar(app, db):
    profile = create_profile("agent-2")
    assert profile.avatar is not None
    assert profile.avatar != ""


def test_create_profile_is_idempotent(app, db):
    first = create_profile("agent-dup", display_name="Echo")
    second = create_profile("agent-dup", display_name="ShouldBeIgnored")
    assert first.id == second.id
    assert first.display_name == second.display_name


def test_create_profile_sets_flavor_text(app, db):
    profile = create_profile("agent-3")
    assert profile.flavor_text is not None


def test_create_profile_starts_at_zero_stats(app, db):
    profile = create_profile("agent-4")
    assert profile.tasks_completed == 0
    assert profile.tasks_failed == 0
