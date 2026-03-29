"""Utility functions for Planet Maiko."""

from datetime import datetime, timezone, timedelta


def time_ago(dt):
    """Format a datetime as a human-readable relative time string."""
    if dt is None:
        return "never"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    diff = now - dt

    if diff < timedelta(minutes=1):
        return "just now"
    if diff < timedelta(hours=1):
        mins = int(diff.total_seconds() / 60)
        return f"{mins}m ago"
    if diff < timedelta(days=1):
        hours = int(diff.total_seconds() / 3600)
        return f"{hours}h ago"
    if diff < timedelta(days=7):
        days = diff.days
        return f"{days}d ago"
    return dt.strftime("%b %d")


def slugify(text, max_len=60):
    """Turn text into a URL-safe slug."""
    slug = text.lower()
    slug = "".join(c if c.isalnum() or c == " " else "" for c in slug)
    slug = "-".join(slug.split())
    return slug[:max_len]


def truncate(text, max_len=100, suffix="..."):
    """Truncate text with suffix if too long."""
    if not text or len(text) <= max_len:
        return text or ""
    return text[:max_len - len(suffix)] + suffix
