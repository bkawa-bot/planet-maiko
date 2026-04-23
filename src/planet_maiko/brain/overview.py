"""Home overview — the rolling LLM-generated pane that greets the user.

This module owns the "Home page overview" surface. The flow:

    1. `get_latest_overview()` reads data/overview.json. If it's fresh
       (< max_age_hours), return it. Otherwise call `generate_overview()`.
    2. Before aggregating context, `generate_overview()` drains every
       enabled poller and runs one brain cycle so the state the LLM
       sees is up to date — no "reviewer requested your review" cards
       that actually got handled 30 min ago.
    3. `generate_overview()` then aggregates state (memos, pupdates
       where still meaningful, tasks, schedule, agents, pollers,
       calendar, scene, the user's optional custom add-on prompt) and
       runs the `home-overview` skill as a full Claude Code agent with
       `skip_permissions=True` so every tool works without prompts.
    4. Strict-JSON output is parsed, written atomically to
       data/overview.json, and returned to the caller.

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

# Absolute cap on cache age — regenerate once we pass this. The Pack
# Requests widget handles real-time actionable signals (agent plans,
# reviews, stuck agents, PR re-requests) directly, so the overview
# doesn't need event-aware invalidation — it can stay a rolling
# narrative summary that refreshes at a leisurely cadence.
DEFAULT_MAX_AGE_HOURS = 4

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
    """Today's calendar pupdates (source='calendar').

    Each event is tagged with `when` relative to the current local time
    so the overview prompt can phrase past events in past tense and only
    call future events "upcoming". Without this marker the LLM saw the
    raw event list and occasionally referred to this-morning's standup
    as "coming up" at 5pm.
    """
    from planet_maiko.config import user_now
    from planet_maiko.models.pupdate import Pupdate
    from datetime import datetime as _dt

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

    now_for_compare = now_local.replace(tzinfo=None) if now_local.tzinfo else now_local

    def _classify(start_iso):
        """Return past / now / upcoming based on event start time.

        The calendar poller writes event start under extra.start as an
        ISO string — we compare against local-now (naive) after stripping
        tz for apples-to-apples. Events without a parseable start fall
        back to "unknown" rather than misclassifying.
        """
        if not start_iso:
            return "unknown"
        try:
            dt = _dt.fromisoformat(start_iso)
        except Exception:
            return "unknown"
        if dt.tzinfo is not None:
            dt = dt.astimezone(now_local.tzinfo).replace(tzinfo=None) if now_local.tzinfo else dt.replace(tzinfo=None)
        # Rough "in progress" window of 60 minutes — covers a standup
        # you're mid-way through so the LLM doesn't say it's "coming up".
        delta_min = (now_for_compare - dt).total_seconds() / 60.0
        if delta_min > 60:
            return "past"
        if delta_min >= 0:
            return "now"
        return "upcoming"

    return [
        {
            "id": p.id,
            "time": iso_utc(p.timestamp),
            "when": _classify((p.extra or {}).get("start")),
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


def _available_sprites():
    """Scan for Maiko sprite files the user has dropped in.

    The frontend serves everything under `frontend/public/sprites/` at
    runtime (via the bundled static_dir), and the convention is
    `maiko-<mood>.png` — the dash-suffix is the vibe the user picked
    when saving the file (e.g., `maiko-sleeping.png`, `maiko-demon.png`,
    `maiko-raincoat.png`).

    This scan happens once per overview generation, so adding a new
    sprite file shows up in the next refresh without a restart.
    Returns a list of plain mood names (no `maiko-` prefix, no `.png`
    suffix) to inject into the prompt vocabulary.
    """
    from planet_maiko.paths import static_dir
    import os

    moods = []
    try:
        sprites_dir = os.path.join(static_dir(), "sprites")
        if not os.path.isdir(sprites_dir):
            return moods
        for name in sorted(os.listdir(sprites_dir)):
            if not name.startswith("maiko-"):
                continue
            stem, ext = os.path.splitext(name)
            if ext.lower() not in (".png", ".svg", ".webp"):
                continue
            moods.append(stem[len("maiko-"):])
    except Exception as e:
        logger.debug(f"[overview] sprite scan failed: {e}")
    return moods


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
    weekend_mode = bool(user_cfg.get("weekend_mode"))
    interruptions_today = _interruptions_today()
    budget = user_cfg.get("interruption_budget")
    over_budget = bool(isinstance(budget, int) and budget > 0 and interruptions_today > budget)

    available_sprites = _available_sprites()
    sprite_hint = (
        ", ".join(available_sprites) if available_sprites else "(none available)"
    )

    return {
        "user_name": user_name,
        "current_time": now.strftime("%I:%M %p"),
        "time_bucket": time_bucket,
        "available_sprites": sprite_hint,
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
        # Weekend mode — durable state. Overview voice leans toward
        # "what can wait until Monday" instead of "what needs you now."
        "weekend_mode": "true" if weekend_mode else "false",
        # Interruption budget — visible-only soft cap. Overview voice
        # shifts toward batching when over budget. Null budget disables.
        "interruptions_today": str(interruptions_today),
        "interruption_budget": "none" if budget is None else str(budget),
        "interruption_over_budget": "true" if over_budget else "false",
    }


def _interruptions_today():
    """Count today's high/urgent, non-dismissed pupdates (user-local day).

    An "interruption" is any event loud enough to pull a focused user
    out of what they're doing. Approximated here as priority in
    {urgent, high} with dismissed=False, created since local midnight.
    Read-only — this is purely a surface signal for the overview
    prompt. Nothing is blocked or altered based on it.
    """
    try:
        from datetime import timezone as _tz
        from planet_maiko.config import user_now
        from planet_maiko.models.pupdate import Pupdate

        now_local = user_now()
        midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        midnight_utc = midnight_local.astimezone(_tz.utc).replace(tzinfo=None)
        return (
            Pupdate.query
            .filter(Pupdate.priority.in_(("urgent", "high")))
            .filter(Pupdate.dismissed == False)  # noqa: E712
            .filter(Pupdate.timestamp >= midnight_utc)
            .count()
        )
    except Exception as e:
        logger.debug(f"[overview] interruption count failed: {e}")
        return 0


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


def _overnight_tasks_context(context):
    """Tasks the pack is continuing on past the user's workday end.

    Only populated when the closing window is active — outside that
    window the overnight section is empty, because "what's queued
    overnight" isn't a useful frame at 11am.

    Shape per item:
        {
          "task_id": str,
          "title": str,
          "agent_name": str | None,
          "agent_avatar": str | None,
        }
    """
    if context.get("closing_window") != "true":
        return []

    from planet_maiko.models.task import Task
    from planet_maiko.models.agent_profile import AgentProfile

    active = (
        Task.query
        .filter(Task.status == "in_progress")
        .filter(Task.assigned_agent_id.isnot(None))
        .order_by(Task.updated_at.desc())
        .limit(10)
        .all()
    )
    if not active:
        return []

    agent_ids = {t.assigned_agent_id for t in active if t.assigned_agent_id}
    agents = {a.id: a for a in AgentProfile.query.filter(AgentProfile.id.in_(agent_ids)).all()}
    out = []
    for t in active:
        agent = agents.get(t.assigned_agent_id)
        out.append({
            "task_id": t.id,
            "title": t.title,
            "agent_name": agent.display_name if agent else None,
            "agent_avatar": agent.avatar if agent else None,
        })
    return out


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


_OVERVIEW_CACHE_FILENAME = "overview.json"


def _overview_cache_path():
    """Path to the home-overview JSON cache.

    File (not DB) because the overview is a pure render — there's no
    historical-audit value in keeping old ones, and the cache is a
    single writer / single reader pattern. A row in SkillResult was
    overkill.
    """
    from planet_maiko.paths import data_dir
    return os.path.join(data_dir(), _OVERVIEW_CACHE_FILENAME)


def _read_cached_overview():
    """Return (generated_at_iso, overview_dict) or (None, None).

    None on every failure path (missing, unreadable, corrupt JSON);
    the caller treats None as "regenerate."
    """
    path = _overview_cache_path()
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("[overview] cache read failed (%s); will regen", e)
        return None, None
    return blob.get("generated_at"), blob.get("overview")


def _write_overview_cache(parsed):
    """Atomic write: JSON to `.tmp`, os.replace to final path.

    Returns the iso timestamp we stamped on the blob so the caller
    can hand it back to the frontend without a re-read.
    """
    path = _overview_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    blob = {"generated_at": generated_at, "overview": parsed}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, ensure_ascii=False)
    os.replace(tmp, path)
    return generated_at


def _iso_is_stale(iso_str, max_age_hours):
    """True if `iso_str` is missing/unparseable or older than max_age_hours."""
    if not iso_str:
        return True
    try:
        ts = datetime.fromisoformat(iso_str)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts > timedelta(hours=max_age_hours)


def _prepoll_and_cycle(app):
    """Drain every enabled poller + run one brain cycle so generate
    sees fresh state. Synchronous — overview regen is already a slow
    op, and stale context is worse than a slower render.

    Pollers run in parallel (they hit distinct external services).
    Brain cycle runs after so synthesis/clustering/routing pull in
    anything the pollers just landed. Failures log-and-continue —
    one broken poller shouldn't block the overview.
    """
    import concurrent.futures
    from planet_maiko.pollers.scheduler import _get_pollers
    from planet_maiko.config import load_config
    from planet_maiko.brain.cycle import run as run_brain_cycle

    config = load_config()
    to_run = []
    for name, poller in _get_pollers().items():
        cfg = config.get(name, {}) or {}
        if not cfg.get("enabled", False):
            continue
        to_run.append((name, poller, cfg))

    def _one(name, poller, cfg):
        with app.app_context():
            return poller.run(cfg, db.session)

    if to_run:
        logger.info("[overview] pre-poll: %s", [n for (n, _, _) in to_run])
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, len(to_run)), thread_name_prefix="overview-prepoll"
        ) as ex:
            futures = {ex.submit(_one, n, p, c): n for (n, p, c) in to_run}
            # 90s total wall budget — any single poller that's stuck
            # that long is stuck, and we'd rather regen overview with
            # slightly-stale data from that one source than hang here.
            try:
                for future in concurrent.futures.as_completed(futures, timeout=90):
                    name = futures[future]
                    try:
                        future.result(timeout=1)
                    except Exception as e:
                        logger.warning("[overview] pre-poll %s failed: %s", name, e)
            except concurrent.futures.TimeoutError:
                logger.warning("[overview] pre-poll overall timeout, proceeding")

    try:
        run_brain_cycle(app)
    except Exception as e:
        logger.warning("[overview] pre-cycle failed: %s", e)


def generate_overview():
    """Run the home-overview skill and persist the result to the
    on-disk JSON cache.

    Flow: prepoll every enabled poller + run one brain cycle (so
    context is fresh), aggregate state into the skill prompt, run the
    `home-overview` skill through Claude Code, parse JSON, stamp it
    onto data/overview.json.

    Raises RuntimeError if the runtime is unavailable or the LLM call
    fails; raises ValueError if the response can't be parsed as JSON.
    The caller is responsible for turning those into HTTP responses.
    """
    from flask import current_app
    from planet_maiko.agents.skills import get_skill_prompt

    app = current_app._get_current_object()
    _prepoll_and_cycle(app)

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
    parsed.setdefault("sprite", None)

    # Validate sprite pick: only keep it if the LLM picked a mood
    # the user actually has a file for. Cheaper than having the
    # frontend retry on a 404 onError, and matches the prompt's
    # "never invent a mood name" rule.
    if parsed.get("sprite"):
        valid = set(_available_sprites())
        if parsed["sprite"] not in valid:
            parsed["sprite"] = None

    # Evening wrap: during the closing window, inject a structured list
    # of tasks the pack will continue on overnight. Deterministic (not
    # LLM-generated) so the frontend can reliably render task-linked
    # rows with agent names. Empty list outside the window or when
    # nothing is queued.
    parsed["overnight"] = _overnight_tasks_context(context)

    generated_at = _write_overview_cache(parsed)

    # Log token usage if the runtime surfaces it.
    if isinstance(result, dict) and result.get("usage"):
        logger.info("[overview] usage: %s", result.get("usage"))

    logger.info("[overview] generated (at=%s)", generated_at)
    return parsed


def get_latest_overview(max_age_hours=DEFAULT_MAX_AGE_HOURS):
    """Return the most recent overview, regenerating if stale / missing.

    Single staleness trigger: cache older than ``max_age_hours``
    (default 4). The Pack Requests widget handles real-time actionable
    signals, so the overview doesn't regen early on incoming pupdates
    — it's a rolling narrative, not an alert surface.

    Returns:
        dict with:
            overview: the parsed JSON the LLM produced
            generated_at: ISO timestamp the cache file was written
            stale: True iff we had to regenerate on this call
    """
    generated_at, overview = _read_cached_overview()

    if overview is None or _iso_is_stale(generated_at, max_age_hours):
        parsed = generate_overview()
        fresh_at, _ = _read_cached_overview()
        return {
            "overview": parsed,
            "generated_at": fresh_at,
            "stale": True,
        }

    return {
        "overview": overview,
        "generated_at": generated_at,
        "stale": False,
    }
