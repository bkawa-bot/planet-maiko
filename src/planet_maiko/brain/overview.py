"""Home overview — the rolling LLM-generated pane that greets the user.

This module owns the "Home page overview" surface. The flow:

    1. `get_latest_overview()` reads data/overview.json. If it's fresh
       (< max_age_hours), return it. Otherwise call `generate_overview()`.
    2. Before aggregating context, `generate_overview()` drains every
       enabled poller and runs one brain cycle so the state the LLM
       sees is up to date — no "reviewer requested your review" cards
       that actually got handled 30 min ago.
    3. `generate_overview()` then aggregates state (memos, pupdates
       where still meaningful, tasks, agents, pollers,
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
import threading
from datetime import datetime, timezone, timedelta

from planet_maiko.database import db, iso_utc

logger = logging.getLogger(__name__)

# Serialize regeneration so concurrent /api/home/overview requests
# (frontend re-mount, Tauri lifecycle, manual refresh-while-in-flight)
# don't fire two LLM runs against the same stale cache. Module-global
# so every Flask request thread shares it.
_overview_lock = threading.Lock()

# Tracks whether a background regen is currently in flight. Module-
# global so a stale-cache GET on Home doesn't queue a second regen
# behind the first one — the user just gets the stale cache while
# the running thread finishes.
_regen_in_flight = threading.Event()

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


def _memos_context():
    """Live memos (status ∈ pending|seen) as the LLM-facing context.

    Memos are the persistent user-facing state — they stick around
    until the user handles them, so no time cutoff. Pupdates don't
    enter the prompt at all anymore: they're ephemeral queue events,
    their effects live on tasks / memos / agent jobs by the time the
    overview regenerates.
    """
    from planet_maiko.models.memo import Memo

    rows = (
        Memo.query
        .filter(Memo.status.in_(("pending", "seen")))
        .order_by(Memo.created_at.desc())
        .limit(MAX_PUPDATES)
        .all()
    )
    return [
        {
            "id": m.id,
            "created_at": iso_utc(m.created_at),
            "kind": m.kind,
            "category": m.category,
            "priority": m.priority,
            "title": _trim(m.title, 300),
            "body": _trim(m.body or "", PUPDATE_BODY_CHARS),
            "url": m.url,
            "cta_label": m.cta_label,
            "cta_action": m.cta_action,
            "source_agent_id": m.source_agent_id,
            "source_task_id": m.source_task_id,
        }
        for m in rows
    ]


def _tasks_context():
    """Active tasks (new / in_progress / blocked) for the overview.

    Skips tasks whose parent project is in a terminal state — the
    cascade in projects.py already cancels their status, but a new
    task created against an already-closed project would otherwise
    still surface here.
    """
    from planet_maiko.models.task import Task
    from planet_maiko.models.project import Project

    closed_project_ids = {
        p.id for p in Project.query
        .filter(Project.status.in_(("done", "cancelled")))
        .all()
    }

    rows = (
        Task.query
        .filter(Task.status.in_(("new", "in_progress", "blocked")))
        .order_by(Task.updated_at.desc())
        .limit(MAX_TASKS * 2)  # overshoot; filter below may drop some
        .all()
    )
    rows = [t for t in rows if not (t.project_id and t.project_id in closed_project_ids)]
    rows = rows[:MAX_TASKS]

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
        ISO string. We compare against local-now (naive) after stripping
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
        # Rough "in progress" window of 60 minutes, covers a standup
        # you're mid-way through so the LLM doesn't say it's "coming up".
        delta_min = (now_for_compare - dt).total_seconds() / 60.0
        if delta_min > 60:
            return "past"
        if delta_min >= 0:
            return "now"
        return "upcoming"

    def _local_time_str(start_iso):
        """Format event start as the user's local time, e.g. "10:30 AM".

        Pre-formatting in the user's tz avoids the LLM treating a UTC
        ISO string as local time (presenting a 10:30 AM Pacific meeting
        as 5:30 PM). Falls back to "" if the start is missing or
        unparseable, so the LLM omits the time rather than printing junk.
        """
        if not start_iso:
            return ""
        try:
            dt = _dt.fromisoformat(start_iso)
        except Exception:
            return ""
        if dt.tzinfo is not None and now_local.tzinfo is not None:
            dt = dt.astimezone(now_local.tzinfo)
        return dt.strftime("%I:%M %p").lstrip("0")

    return [
        {
            "id": p.id,
            "time": _local_time_str((p.extra or {}).get("start")),
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
    """Snapshot of brain-cycle health for the system status pane."""
    try:
        from flask import current_app
        from planet_maiko.plugins.loader import get_plugins
        from planet_maiko.plugins.poller import PollerPlugin
        from datetime import datetime as _dt, timezone as _tz

        plugins = {}
        for p in get_plugins():
            if not isinstance(p, PollerPlugin):
                continue
            last = p._last_polled
            plugins[p.name] = {
                "last_run_at": (
                    _dt.fromtimestamp(last, _tz.utc).isoformat()
                    if last else None
                ),
            }
        return {
            "scheduler_running": True,
            "pollers": plugins,
            "last_brain_cycle": current_app.config.get("LAST_BRAIN_CYCLE"),
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

    available_sprites = _available_sprites()
    sprite_hint = (
        ", ".join(available_sprites) if available_sprites else "(none available)"
    )

    return {
        "user_name": user_name,
        "current_time": now.strftime("%I:%M %p"),
        "time_bucket": time_bucket,
        "available_sprites": sprite_hint,
        "memos": json.dumps(_memos_context(), indent=2, default=str),
        "tasks": json.dumps(_tasks_context(), indent=2, default=str),
        "calendar": json.dumps(_calendar_context(), indent=2, default=str),
        "agents": json.dumps(_agents_context(), indent=2, default=str),
        "pollers": json.dumps(_pollers_context(), indent=2, default=str),
        "scene": json.dumps(_scene_context(), indent=2, default=str),
        "custom_prompt": _custom_prompt() or "(no add-on configured)",
    }


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
    from planet_maiko.agents.routing import resolve_model, resolve_effort

    # task_type="skill:home-overview" routes to OllamaRuntime by
    # default (cheaper than Opus for the daily prose). Falls back to
    # the brain.runtime default if Ollama isn't running.
    runtime = _get_runtime("skill:home-overview")
    if not runtime.is_available():
        raise RuntimeError("Brain runtime not available")

    # resolve_model now takes runtime.name so users who set per-runtime
    # model overrides (routing.runtime_models.ollama.skill:home-overview
    # = "llama3.1:70b") get them honored. Without that override it
    # falls through to the global rules → DEFAULT_ROUTING → the
    # runtime's own default.
    model = (resolve_model("skill:home-overview", runtime.name)
             or resolve_model("skill", runtime.name))
    effort = resolve_effort("skill:home-overview") or resolve_effort("skill")

    # Release DB before the long call so SQLite writers aren't blocked
    # while Claude Code is working.
    db.session.close()
    return runtime.send(
        prompt,
        working_dir=working_dir,
        timeout=OVERVIEW_TIMEOUT_SECONDS,
        model=model,
        effort=effort,
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
    """Force every enabled poller plugin to refresh, then run one brain
    cycle so generate sees fresh state. Synchronous; overview regen is
    already a slow op and stale context is worse than a slower render.

    Plugins run in parallel (they hit distinct external services).
    Brain cycle runs after so synthesis/clustering/routing pull in
    anything the plugins just landed. Failures log-and-continue.
    """
    import concurrent.futures
    from planet_maiko.plugins.loader import get_plugins
    from planet_maiko.plugins.poller import PollerPlugin
    from planet_maiko.brain.cycle import run as run_brain_cycle

    to_run = [p for p in get_plugins() if isinstance(p, PollerPlugin)]

    if to_run:
        logger.info("[overview] pre-poll: %s", [p.name for p in to_run])
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, len(to_run)), thread_name_prefix="overview-prepoll"
        ) as ex:
            futures = {ex.submit(p.force_poll, app): p.name for p in to_run}
            # 90s overall budget. A single poller stuck longer than that
            # is stuck, and we'd rather regen overview with slightly-stale
            # data from one source than hang.
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


def _request_agent_status_updates(timeout_s=20):
    """Wake every running AgentJob and wait briefly for a status reply.

    Called at the start of overview generation so the LLM (and the
    home page's Working agents widget) sees fresh agent voices rather
    than whatever was last said. Best-effort:
      - Agents already mid-work get drop-on-busy (the wake_agent
        source 'status_request' is in _DROP_ON_BUSY_SOURCES).
      - Agents that don't reply within timeout_s contribute their
        stale last_message to the overview.

    Returns the count of agents that posted a fresh status during the
    wait. Bounded blocking is OK here — overview generation runs on a
    daemon thread, so the user never sees the wait.
    """
    from datetime import datetime, timezone
    import time

    try:
        from planet_maiko.models.agent_job import AgentJob
        from planet_maiko.models.agent_message import AgentMessage
        from planet_maiko.agents.wake import wake_agent, is_working
        from planet_maiko.api.agent_outbox import FOLLOWUP_KINDS
    except Exception as e:
        logger.debug(f"[overview] agent imports failed: {e}")
        return 0

    threshold = datetime.now(timezone.utc)
    # Alive = running agents OR FOLLOWUP_KINDS agents parked in `done`
    # with their worktree preserved (review / investigation / cartograph
    # post-ready_for_review). Without the latter, the most common case
    # -- a finished one-shot agent waiting on the user -- never gets a
    # status request, which is the bulk of the pack on a given day.
    candidates = (
        AgentJob.query
        .filter(AgentJob.status.in_(("running", "done")))
        .all()
    )
    running = [
        j for j in candidates
        if j.status == "running"
        or (j.kind in FOLLOWUP_KINDS and j.worktree_path)
    ]
    if not running:
        return 0

    prompt = (
        "The user just walked into the town square — they're checking in, "
        "and they can see you from across the way. This is your coffee-"
        "machine moment, not a status report.\n\n"
        "Say hi the way your character would (a wave, a nod, a weird "
        "non-sequitur, whatever fits) and tell them in one beat what "
        "you're chewing on right now, plus anything you'd love their "
        "eyes on — a question, a blocker, a fresh diff, a thought you "
        "had on the bus. If you're deep in flow and don't need anything, "
        "just say so in a way that sounds like you. Show personality. "
        "Be funny if the moment lands; be quiet if it doesn't. Brief — "
        "one sentence, two if the second one really earns it.\n\n"
        "Reply via reply(message_type='status'). Then get back to it."
    )

    waited_on = []
    for job in running:
        if is_working(job.id):
            continue
        try:
            ok, mode = wake_agent(job.id, prompt, source="status_request")
            if ok and mode == "woke":
                waited_on.append(job.id)
        except Exception as e:
            logger.debug(f"[overview] status wake failed for {job.id}: {e}")

    if not waited_on:
        return 0

    logger.info(
        f"[overview] requesting status from {len(waited_on)} agent(s), "
        f"waiting up to {timeout_s}s"
    )
    received = set()
    start = time.time()
    while time.time() - start < timeout_s:
        new_msgs = (
            AgentMessage.query
            .filter(AgentMessage.task_id.in_(waited_on))
            .filter(AgentMessage.direction == "from_agent")
            .filter(AgentMessage.created_at >= threshold)
            .all()
        )
        received.update(m.task_id for m in new_msgs)
        if len(received) >= len(waited_on):
            break
        time.sleep(2)

    logger.info(
        f"[overview] {len(received)}/{len(waited_on)} agent(s) replied with status"
    )
    return len(received)


def generate_overview():
    """Run the home-overview skill and persist the result to the
    on-disk JSON cache.

    Flow: prepoll every enabled poller + run one brain cycle (so
    context is fresh), aggregate state into the skill prompt, run the
    `home-overview` skill through Claude Code, parse JSON, stamp it
    onto data/overview.json.

    Serialized via `_overview_lock` so two concurrent regen requests
    don't fire two LLM runs. Refresh callers that arrive while a
    generation is in flight wait for it to finish; this is acceptable
    because the work they wanted (a fresh overview) is already happening.

    Raises RuntimeError if the runtime is unavailable or the LLM call
    fails; raises ValueError if the response can't be parsed as JSON.
    The caller is responsible for turning those into HTTP responses.
    """
    with _overview_lock:
        return _generate_overview_locked()


def _generate_overview_locked():
    """Actual generation. Caller must hold `_overview_lock`."""
    from flask import current_app
    from planet_maiko.agents.skills import get_skill_prompt

    app = current_app._get_current_object()
    _prepoll_and_cycle(app)

    # Ask every running agent for a fresh status before we build the
    # context. This is the "live wake" architecture: we'd rather
    # hold up the overview by ~15-30s and have the LLM weave in
    # current agent voices than render a stale snapshot.
    try:
        _request_agent_status_updates(timeout_s=20)
    except Exception as e:
        logger.warning(f"[overview] agent status fan-out skipped: {e}")

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
    parsed.setdefault("sprite", None)

    # Validate sprite pick: only keep it if the LLM picked a mood
    # the user actually has a file for. Cheaper than having the
    # frontend retry on a 404 onError, and matches the prompt's
    # "never invent a mood name" rule.
    if parsed.get("sprite"):
        valid = set(_available_sprites())
        if parsed["sprite"] not in valid:
            parsed["sprite"] = None

    generated_at = _write_overview_cache(parsed)

    # Log token usage if the runtime surfaces it.
    if isinstance(result, dict) and result.get("usage"):
        logger.info("[overview] usage: %s", result.get("usage"))

    logger.info("[overview] generated (at=%s)", generated_at)
    return parsed


def _placeholder_overview():
    """Minimal overview shape for the no-cache-yet case. Frontend
    renders this without crashing while the first real generation
    catches up on a daemon thread."""
    return {
        "greeting": "Hi 🐾",
        "summary": "Maiko is preparing your overview — refresh in a minute.",
        "focus": [],
        "needs": [],
        "alive": "",
        "custom_section": "",
        "sprite": None,
    }


def _regen_in_background(app):
    """Spawn the (slow) overview regeneration on a daemon thread so
    GET /api/home/overview never blocks on it. Idempotent — returns
    fast if a regen is already in flight, so a flurry of stale-cache
    requests can't queue up parallel LLM runs."""
    if _regen_in_flight.is_set():
        return
    _regen_in_flight.set()

    def _runner():
        try:
            with app.app_context():
                with _overview_lock:
                    _generate_overview_locked()
        except Exception as e:
            logger.warning("[overview] background regen failed: %s", e)
        finally:
            _regen_in_flight.clear()

    threading.Thread(
        target=_runner, daemon=True, name="overview-regen"
    ).start()


def get_latest_overview(max_age_hours=DEFAULT_MAX_AGE_HOURS):
    """Return the most recent overview without ever blocking on regen.

    Three cases:
      - Fresh cache: return as-is, stale=False.
      - Stale cache: return the stale data immediately (stale=True)
        and kick a background regen so the next read sees the fresh
        version. Better to let the user keep working with yesterday's
        narrative than to hang the request for ~1-2 minutes on a
        full pre-poll + cycle + LLM round-trip.
      - No cache (first load on a fresh install): return a
        placeholder shape, kick regen, and let the frontend poll.

    Single staleness trigger: cache older than ``max_age_hours``
    (default 4). The Pack Requests widget handles real-time
    actionable signals, so the overview doesn't regen early on
    incoming pupdates — it's a rolling narrative, not an alert surface.

    Returns:
        dict with overview / generated_at / stale.
    """
    from flask import current_app

    generated_at, overview = _read_cached_overview()
    if overview is not None and not _iso_is_stale(generated_at, max_age_hours):
        return {"overview": overview, "generated_at": generated_at, "stale": False}

    # Stale or missing — fire regen async and serve what we have now.
    try:
        _regen_in_background(current_app._get_current_object())
    except Exception as e:
        # No app context (e.g. CLI invocation). Fall through to the
        # synchronous path below; nothing else can run the regen.
        logger.debug("[overview] async regen unavailable: %s", e)
        with _overview_lock:
            parsed = _generate_overview_locked()
            fresh_at, _ = _read_cached_overview()
            return {"overview": parsed, "generated_at": fresh_at, "stale": True}

    if overview is not None:
        return {"overview": overview, "generated_at": generated_at, "stale": True}

    return {
        "overview": _placeholder_overview(),
        "generated_at": None,
        "stale": True,
    }
