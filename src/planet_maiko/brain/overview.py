"""Home overview — the rolling LLM-generated pane that greets the user.

This module owns the "Home page overview" surface. The flow:

    1. `get_latest_overview()` looks up the most recent
       SkillResult(skill_name="home-overview"). If it's fresh (< max_age_hours),
       return it. Otherwise call `generate_overview()`.
    2. `generate_overview()` aggregates every relevant piece of state —
       pupdates, tasks, schedule, agents, pollers, calendar, scene, and
       the user's optional custom add-on prompt — and runs the
       `home-overview` skill as a full Claude Code agent with
       `skip_permissions=True` so every tool (Bash, Read, WebFetch,
       MCPs) works without permission prompts.
    3. The skill is instructed to emit strict JSON. We parse it
       robustly, stash it as a `SkillResult.content` string, and return
       the parsed dict.

Why a full agent and not `send_json`? The whole point of the overview
is that Maiko can *look things up* — Slack threads, a Linear issue, a
URL a pupdate references. `send_json` wraps a one-shot text exchange;
we want tool use. Using the skill path with `skip_permissions=True` is
the same pattern the one-shot coding / review agents use.

Scratch workspace: a persistent dir under the XDG data home
(`~/.local/share/planet-maiko/overview-workspace/`). Persistent over
per-run-temp because the LLM will occasionally want to keep a small
cached file around (e.g. a spelunking bash one-liner's output) and the
dir is isolated from any real repo so nothing of value can be
corrupted. Cleanup is best-effort and bounded — see `_ensure_workspace`.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db, iso_utc

logger = logging.getLogger(__name__)

# Absolute cap on cache age — regenerate once we pass this, regardless
# of whether anything interesting has happened. Event-aware invalidation
# (below) can trigger regen earlier when actionable pupdates arrive.
DEFAULT_MAX_AGE_HOURS = 4

# Floor for event-aware regeneration. When actionable pupdates arrive
# AFTER a cached overview, we regenerate — but only if the cache is at
# least this many minutes old. Stops a burst of pupdates from thrashing
# the generator.
EVENT_INVALIDATION_FLOOR_MINUTES = 30

# Pupdate types that trigger event-aware regen when they arrive after
# the current cache. Kept in sync by hand with the frontend's
# WAITING_TYPES set — if new actionable types get added there, add
# them here too so overview refreshes catch them.
ACTIONABLE_PUPDATE_TYPES = frozenset({
    "agent_plan_for_approval",
    "agent_ready_for_review",
    "agent_stuck",
    "agent_proposal",
    "pr_review_requested",
    "pr_changes_requested",
    "pr_review_complete",
    "investigation_complete",
})

# Tool-using LLM runs take real wall time. Slack / Linear / WebFetch
# each add a round-trip; the model reasons over the JSON aggregate;
# occasionally the add-on asks for a bash sweep. 180s is too tight if
# the user wires up a chatty add-on. 300s is the working floor.
OVERVIEW_TIMEOUT_SECONDS = 300

# Caps to keep the prompt from ballooning when the user has a chaotic
# inbox. Picked by eyeballing current volumes — 30 pupdates at ~300
# char bodies is ~9KB, which is comfortable alongside the ~1KB prompt
# frame. If you see "prompt too long" errors, drop these first.
MAX_PUPDATES = 30
MAX_TASKS = 30
MAX_AGENTS = 15
PUPDATE_BODY_CHARS = 300


# ---------------------------------------------------------------------------
# Scratch workspace
# ---------------------------------------------------------------------------


def _workspace_dir():
    """Return the on-disk path of the persistent overview scratch dir.

    Lives under XDG data home so it survives across runs (the LLM can
    re-use a small cache), but is isolated from any real repo. The
    caller is responsible for ensuring it exists before using it; use
    `_ensure_workspace()` for that.
    """
    from planet_maiko.paths import data_dir
    return os.path.join(data_dir(), "overview-workspace")


def _ensure_workspace():
    """Create the scratch workspace if missing and prune obvious bloat.

    Prunes any single file larger than 10 MB — we don't want a
    runaway bash-loop-of-death filling the user's disk. Best-effort;
    silent failures are fine because the LLM can always fall back to
    in-memory work.
    """
    path = _workspace_dir()
    os.makedirs(path, exist_ok=True)
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if os.path.isfile(full) and os.path.getsize(full) > 10 * 1024 * 1024:
                os.remove(full)
    except Exception:
        pass
    return path


# ---------------------------------------------------------------------------
# Context aggregation
# ---------------------------------------------------------------------------


def _trim(text, limit):
    """Shorten `text` to `limit` chars with a trailing ellipsis."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"


def _pupdates_context():
    """Non-dismissed pupdates from the last 24h, JSON-serialisable."""
    from planet_maiko.models.pupdate import Pupdate

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (
        Pupdate.query
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .filter(Pupdate.timestamp >= cutoff)
        .order_by(Pupdate.timestamp.desc())
        .limit(MAX_PUPDATES)
        .all()
    )
    return [
        {
            "id": p.id,
            "timestamp": iso_utc(p.timestamp),
            "source": p.source,
            "type": p.type,
            "priority": p.priority,
            "category": p.category or "activity",
            "title": _trim(p.title, 300),
            "body": _trim(p.body or "", PUPDATE_BODY_CHARS),
            "url": p.url,
            "actionable": bool(p.actionable),
            "action_hint": p.action_hint,
            "tags": p.tags or [],
        }
        for p in rows
    ]


def _tasks_context():
    """Active tasks (new / in_progress / blocked) for the overview."""
    from planet_maiko.models.task import Task

    rows = (
        Task.query
        .filter(Task.status.in_(("new", "in_progress", "blocked")))
        .order_by(Task.updated_at.desc())
        .limit(MAX_TASKS)
        .all()
    )
    return [
        {
            "id": t.id,
            "title": _trim(t.title, 300),
            "type": t.type,
            "status": t.status,
            "priority": t.priority,
            "due_date": t.due_date,
            "url": t.url,
            "assigned_agent_id": t.assigned_agent_id,
            "repo": (t.extra or {}).get("repo"),
            "pinned": bool((t.extra or {}).get("pinned")),
        }
        for t in rows
    ]


def _schedule_context():
    """Schedule / focus order — same thing /api/brain/schedule returns."""
    try:
        from planet_maiko.brain.tasks.scheduler import compute_schedule
        return compute_schedule()
    except Exception as e:
        logger.debug(f"[overview] schedule fetch failed: {e}")
        return {"blocks": [], "total_hours": 0, "task_count": 0}


def _calendar_context():
    """Today's calendar pupdates (source='calendar')."""
    from planet_maiko.config import user_now
    from planet_maiko.models.pupdate import Pupdate

    now_local = user_now()
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = midnight_local + timedelta(days=1)
    start_utc = midnight_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)

    rows = (
        Pupdate.query
        .filter(Pupdate.source == "calendar")
        .filter(Pupdate.timestamp >= start_utc)
        .filter(Pupdate.timestamp < end_utc)
        .order_by(Pupdate.timestamp.asc())
        .limit(30)
        .all()
    )
    return [
        {
            "id": p.id,
            "time": iso_utc(p.timestamp),
            "title": _trim(p.title, 300),
            "body": _trim(p.body or "", 400),
            "url": p.url,
        }
        for p in rows
    ]


def _agents_context():
    """Active agent profiles + a snippet of their recent activity."""
    try:
        from planet_maiko.models.agent_profile import AgentProfile
        from planet_maiko.agents.monitor import get_agent_activity
    except Exception:
        return {"profiles": [], "activity": []}

    profiles = (
        AgentProfile.query
        .filter((AgentProfile.archived == False) | (AgentProfile.archived.is_(None)))  # noqa: E712
        .order_by(AgentProfile.last_active_at.desc().nullslast())
        .limit(MAX_AGENTS)
        .all()
    )
    try:
        activity = get_agent_activity()
    except Exception as e:
        logger.debug(f"[overview] agent activity fetch failed: {e}")
        activity = []
    return {
        "profiles": [
            {
                "id": p.id,
                "display_name": p.display_name,
                "avatar": p.avatar,
                "role": p.role or "coding",
                "scope_repo": p.scope_repo,
                "tasks_completed": p.tasks_completed or 0,
                "last_active_at": iso_utc(p.last_active_at),
            }
            for p in profiles
        ],
        "activity": activity[:MAX_AGENTS],
    }


def _pollers_context():
    """Snapshot of poller health, matching /api/system/health."""
    try:
        from flask import current_app
        scheduler = current_app.config.get("SCHEDULER")
        if scheduler is None:
            return {"scheduler_running": False, "pollers": {}, "last_brain_cycle": None}
        return {
            "scheduler_running": True,
            "pollers": dict(scheduler.poller_status),
            "last_brain_cycle": scheduler.last_brain_cycle,
        }
    except Exception as e:
        logger.debug(f"[overview] poller health fetch failed: {e}")
        return {"scheduler_running": False, "pollers": {}, "last_brain_cycle": None}


def _scene_context():
    """Weather / season / time-of-day snapshot; best-effort."""
    try:
        from planet_maiko.config import load_config
        cfg = load_config().get("scene", {})
        lat = cfg.get("latitude")
        lon = cfg.get("longitude")
        # Import here to avoid pulling urllib at module load.
        from planet_maiko.brain.creativity.scene import generate
        if lat is not None and lon is not None:
            # Re-use the cached weather fetcher from scene_api if
            # possible, but don't depend on the blueprint being loaded.
            try:
                from planet_maiko.api.scene_api import _fetch_weather
                live = _fetch_weather(lat, lon) or {"weather": "clear", "temperature_f": 70}
            except Exception:
                live = {"weather": "clear", "temperature_f": 70}
            latitude = lat
        else:
            live = {"weather": "clear", "temperature_f": 70}
            latitude = 37.7
        scene = generate(weather=live["weather"], temperature_f=live["temperature_f"], latitude=latitude)
        return {
            "weather": scene["context"].get("weather"),
            "temperature_f": scene["context"].get("temperature_f"),
            "season": scene["context"].get("season"),
            "time_bucket": scene["context"].get("time_bucket"),
            "holiday": scene["context"].get("holiday"),
            "mood": scene["scene"].get("mood"),
        }
    except Exception as e:
        logger.debug(f"[overview] scene fetch failed: {e}")
        return {}


def _custom_prompt():
    """User's optional add-on prompt injected into the custom_section.

    Lives at `config.overview.custom_prompt`. If the key is missing or
    whitespace, returns "" — the prompt file says "empty -> empty
    custom_section". The Settings UI that writes this key is a separate
    follow-up commit; this module just reads it.
    """
    try:
        from planet_maiko.config import load_config
        cfg = load_config().get("overview", {}) or {}
        return (cfg.get("custom_prompt") or "").strip()
    except Exception:
        return ""


def _time_bucket_for(dt):
    """Same bucket mapping the scene engine uses — kept in sync by import."""
    from planet_maiko.brain.creativity.scene import _time_bucket
    return _time_bucket(dt.hour)


def _build_context():
    """Aggregate everything the prompt needs into format-string values.

    Returns a dict ready to hand to `get_skill_prompt`. Values are
    pre-serialised JSON strings (so the prompt can just `{pupdates}`).
    """
    from planet_maiko.config import load_config, user_now

    now = user_now()
    time_bucket = _time_bucket_for(now)
    user_cfg = {}
    try:
        user_cfg = (load_config().get("user", {}) or {})
    except Exception:
        pass
    user_name = (user_cfg.get("name") or "").strip() or "there"

    closing_window, closing_reason = _closing_window_info(now, user_cfg)

    return {
        "user_name": user_name,
        "current_time": now.strftime("%I:%M %p"),
        "time_bucket": time_bucket,
        "pupdates": json.dumps(_pupdates_context(), indent=2, default=str),
        "tasks": json.dumps(_tasks_context(), indent=2, default=str),
        "schedule": json.dumps(_schedule_context(), indent=2, default=str),
        "calendar": json.dumps(_calendar_context(), indent=2, default=str),
        "agents": json.dumps(_agents_context(), indent=2, default=str),
        "pollers": json.dumps(_pollers_context(), indent=2, default=str),
        "scene": json.dumps(_scene_context(), indent=2, default=str),
        "custom_prompt": _custom_prompt() or "(no add-on configured)",
        # Closing-condition signal — the LLM uses these to decide
        # whether to include a `closing` section in its output.
        "closing_window": "true" if closing_window else "false",
        "closing_reason": closing_reason,
        "shipped_today": json.dumps(_shipped_today_context(), indent=2, default=str),
    }


def _closing_window_info(now, user_cfg):
    """Decide whether we're in the "enough for today" window.

    Returns (bool, reason_string). The window opens 30 min before
    workday_end_hour and stays open for 2h after, so anyone checking
    Maiko between 4:30pm and 7pm (with default 5pm end_hour) gets the
    closing reflection. Outside that window the field is suppressed.

    Honors user.workday_end_hour; None disables the feature entirely.
    """
    end_hour = user_cfg.get("workday_end_hour")
    if end_hour is None or not isinstance(end_hour, int) or not (0 <= end_hour <= 23):
        return False, ""
    end_minutes = end_hour * 60
    now_minutes = now.hour * 60 + now.minute
    # Open the window at end_hour - 30 min, close at end_hour + 2h.
    opens = end_minutes - 30
    closes = end_minutes + 120
    if opens <= now_minutes <= closes:
        return True, f"workday winding down around {end_hour:02d}:00 local"
    return False, ""


def _shipped_today_context():
    """Tasks that moved to done / cancelled today (user-local day).

    Gives the LLM the material it needs to write a grounded closing
    reflection — "you shipped X, Y; the Z refactor wraps tomorrow" —
    instead of a generic "good work today" that would make the feature
    feel like cheerleading. The tasks_context above filters to active
    states (new / in_progress / blocked), so done tasks don't show up
    there and we surface them separately here.
    """
    from datetime import timezone as _tz
    from planet_maiko.config import user_now
    from planet_maiko.models.task import Task

    now_local = user_now()
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_utc = midnight_local.astimezone(_tz.utc).replace(tzinfo=None)

    done = (
        Task.query
        .filter(Task.status.in_(("done", "cancelled")))
        .filter(Task.updated_at >= midnight_utc)
        .order_by(Task.updated_at.desc())
        .limit(15)
        .all()
    )
    return [
        {
            "id": t.id,
            "title": t.title,
            "type": t.type,
            "status": t.status,
            "updated_at": iso_utc(t.updated_at),
        }
        for t in done
    ]


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _parse_overview_json(raw):
    """Robustly extract the JSON object from an LLM response.

    Tries, in order:
        1. Parse the whole response as JSON.
        2. Find the first `{` and last `}` and parse that substring.
        3. Extract from a ```json ... ``` fenced block.

    Raises ValueError with a snippet if nothing parses.
    """
    if not raw or not raw.strip():
        raise ValueError("LLM returned an empty response")

    text = raw.strip()

    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. First { to last }
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        substring = text[first : last + 1]
        try:
            return json.loads(substring)
        except json.JSONDecodeError:
            pass

    # 3. Fenced code block
    match = _FENCED_JSON_RE.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    snippet = text[:400] + ("\u2026" if len(text) > 400 else "")
    raise ValueError(f"Could not parse overview JSON from LLM response. Snippet: {snippet!r}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _run_home_overview_skill(prompt, working_dir):
    """Thin wrapper that runs the home-overview skill with full tool access.

    Mirrors `execute_one_shot_task`'s invocation of `run_skill_as_agent`,
    but without a profile (the overview is authored as Maiko, not as a
    specialised agent).
    """
    from planet_maiko.agents.brain_session import _get_runtime
    from planet_maiko.agents.routing import resolve_model

    runtime = _get_runtime()
    if not runtime.is_available():
        raise RuntimeError("Brain runtime not available")

    model = resolve_model("skill:home-overview") or resolve_model("skill")

    # Release DB before the long call so SQLite writers aren't blocked
    # while Claude Code is working.
    db.session.close()
    return runtime.send(
        prompt,
        working_dir=working_dir,
        timeout=OVERVIEW_TIMEOUT_SECONDS,
        model=model,
        skip_permissions=True,
    )


def generate_overview():
    """Run the home-overview skill and persist the result as a SkillResult.

    Aggregates every relevant piece of state, runs the
    `home-overview` skill through the Claude Code runtime with full
    tool permissions, parses the JSON response, and writes it to the
    `skill_results` table (`skill_name="home-overview"`, `content` =
    stringified JSON). Returns the parsed dict.

    Raises RuntimeError if the runtime is unavailable or the LLM call
    fails; raises ValueError if the response can't be parsed as JSON.
    The caller is responsible for turning those into HTTP responses.
    """
    from planet_maiko.agents.skills import get_skill_prompt
    from planet_maiko.config import user_now
    from planet_maiko.models.skill_result import SkillResult

    context = _build_context()
    prompt = get_skill_prompt("home-overview", context)
    if prompt is None:
        raise RuntimeError("home-overview skill is not registered or its prompt is missing")

    working_dir = _ensure_workspace()

    logger.info("[overview] generating (workspace=%s)", working_dir)
    result = _run_home_overview_skill(prompt, working_dir)

    if not result or not result.get("success"):
        err = (result or {}).get("error") or "unknown runtime error"
        logger.warning("[overview] LLM call failed: %s", err)
        raise RuntimeError(f"LLM call failed: {err}")

    output = (result.get("output") or "").strip()
    parsed = _parse_overview_json(output)

    # Defensive defaults — the prompt tells the LLM to emit every key,
    # but if it misses one we'd rather serve a partial overview than
    # 500 at the frontend.
    parsed.setdefault("greeting", "")
    parsed.setdefault("summary", "")
    parsed.setdefault("focus", [])
    parsed.setdefault("needs", [])
    parsed.setdefault("alive", "")
    parsed.setdefault("custom_section", "")
    parsed.setdefault("closing", "")

    now_local = user_now()
    sr = SkillResult(
        skill_name="home-overview",
        title=f"Home Overview — {now_local.strftime('%B %d %H:%M')}",
        content=json.dumps(parsed, ensure_ascii=False),
        context_summary=None,
    )
    db.session.add(sr)
    db.session.commit()

    # Log token usage if the runtime surfaces it (current claude_code
    # runtime doesn't, but future runtimes / SDK integrations might).
    if isinstance(result, dict) and result.get("usage"):
        logger.info("[overview] usage: %s", result.get("usage"))

    logger.info("[overview] generated (skill_result_id=%s)", sr.id)
    return parsed


def _latest_skill_result():
    """Return the most recent home-overview SkillResult row, or None."""
    from planet_maiko.models.skill_result import SkillResult
    return (
        SkillResult.query
        .filter_by(skill_name="home-overview")
        .order_by(SkillResult.created_at.desc())
        .first()
    )


def _is_stale(row, max_age_hours):
    """True if `row` is missing or older than `max_age_hours`."""
    if row is None:
        return True
    created = row.created_at
    if created is None:
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created
    return age > timedelta(hours=max_age_hours)


def _has_new_actionable_pupdate(since_utc):
    """True if any actionable pupdate was created after `since_utc`.

    `since_utc` is a naive UTC datetime — matches how Pupdate.timestamp
    is stored in the DB (see `_calendar_context` for the same pattern).
    Only non-dismissed pupdates count, so an alert the user already
    swept doesn't trigger a regen the next time Home refreshes.
    """
    from planet_maiko.models.pupdate import Pupdate

    q = (
        Pupdate.query
        .filter(Pupdate.type.in_(ACTIONABLE_PUPDATE_TYPES))
        .filter(Pupdate.dismissed == False)  # noqa: E712
        .filter(Pupdate.timestamp > since_utc)
    )
    return db.session.query(q.exists()).scalar()


def get_latest_overview(max_age_hours=DEFAULT_MAX_AGE_HOURS):
    """Return the most recent overview, regenerating if stale / missing.

    Two staleness triggers, in order:
      1. Absolute: cache older than ``max_age_hours`` (default 4).
      2. Event-aware: cache older than
         ``EVENT_INVALIDATION_FLOOR_MINUTES`` (default 30) AND at least
         one actionable pupdate has arrived since the cache was written.

    Returns:
        dict with:
            overview: the parsed JSON the LLM produced
            generated_at: ISO timestamp the result row was written
            stale: True iff we had to regenerate on this call
    """
    row = _latest_skill_result()
    needs_regen = _is_stale(row, max_age_hours)

    # Event-aware check: cache still looks fresh by the clock, but a
    # new actionable pupdate has landed. If we're past the floor, let
    # the user see a fresh overview that reflects it.
    if not needs_regen and row is not None and row.created_at is not None:
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - created
        if age > timedelta(minutes=EVENT_INVALIDATION_FLOOR_MINUTES):
            since_utc = created.astimezone(timezone.utc).replace(tzinfo=None)
            if _has_new_actionable_pupdate(since_utc):
                logger.info(
                    "[overview] event-aware regen triggered (new actionable pupdate since %s)",
                    iso_utc(row.created_at),
                )
                needs_regen = True

    if needs_regen:
        parsed = generate_overview()
        row = _latest_skill_result()
        return {
            "overview": parsed,
            "generated_at": iso_utc(row.created_at) if row else None,
            "stale": True,
        }

    try:
        overview = json.loads(row.content)
    except (TypeError, ValueError) as e:
        # Stored content isn't parseable — treat as missing and regenerate.
        logger.warning("[overview] cached content unparseable, regenerating: %s", e)
        parsed = generate_overview()
        row = _latest_skill_result()
        return {
            "overview": parsed,
            "generated_at": iso_utc(row.created_at) if row else None,
            "stale": True,
        }

    return {
        "overview": overview,
        "generated_at": iso_utc(row.created_at),
        "stale": False,
    }
