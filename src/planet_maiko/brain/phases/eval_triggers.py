"""Workflow phase: fire flow runs from trigger nodes.

A trigger node (kind="trigger", config.pupdate_type) on a saved flow makes
that flow event-driven: a pupdate newer than the flow's watermark that
matches the trigger starts a run seeded with the pupdate. This is the
automation engine reborn as "start a flow from an event" — the first cut of
folding automations into workflows.

Runs on the WORKER thread (alongside advance + execute) so triggering is
fast and isn't starved when the full brain cycle is slow. Dedup is the
watermark: each pupdate sits after a flow's watermark exactly once, and the
watermark advances to "now" each eval. Multiple flows can fire on one pupdate
(each keeps its own watermark). A per-flow cap bounds a pupdate burst.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# A single eval won't start more than this many runs for one flow (a pupdate
# burst shouldn't spawn an unbounded pile of agent sessions at once).
_MAX_STARTS_PER_FLOW = 10


def _pupdate_input(p):
    """The text fed to the flow's entry node: the pupdate's title + body."""
    parts = [p.title or ""]
    if p.body:
        parts.append(p.body)
    text = "\n\n".join(x for x in parts if x).strip()
    return text or (p.title or p.type or "")


def _pupdate_repo(p):
    """Best-effort repo for the run (so a downstream coder gets a checkout).
    Pupdates carry no first-class repo; look in the metadata."""
    extra = p.extra or {}
    return extra.get("repo") or extra.get("scope_repo") or None


def _matches(p, cfg):
    """Does this pupdate match a trigger node's config? Type is the primary
    filter; optional priority / source narrow it. Empty type matches any."""
    want_type = (cfg.get("pupdate_type") or "").strip()
    if want_type and p.type != want_type:
        return False
    want_priority = (cfg.get("priority") or "").strip()
    if want_priority and (p.priority or "") != want_priority:
        return False
    want_source = (cfg.get("source") or "").strip()
    if want_source and (p.source or "") != want_source:
        return False
    return True


def _interval_minutes(cfg):
    """Minutes between fires for a schedule trigger (interval_value +
    interval_unit). Defaults to 1 hour; floors at 1 minute."""
    try:
        val = max(1, int(cfg.get("interval_value") or 1))
    except (TypeError, ValueError):
        val = 1
    unit = (cfg.get("interval_unit") or "hours").strip()
    return val * {"minutes": 1, "hours": 60, "days": 1440}.get(unit, 60)


def _schedule_due(cfg, last, now):
    """Whether a schedule trigger should fire now. `last` is the flow's
    last-fired time (aware UTC or None), `now` is aware UTC. Kind "clock"
    fires at a wall-clock time; anything else is an interval cadence."""
    if (cfg.get("schedule_kind") or "interval").strip() == "clock":
        return _clock_due(cfg, last, now)
    interval_min = _interval_minutes(cfg)
    return last is None or (now - last).total_seconds() >= interval_min * 60


def _clock_due(cfg, last, now):
    """Clock-time cadence: fire once when we're at/past today's HH:MM (in the
    server's local timezone, which is the user's since Maiko runs locally) on
    an allowed weekday, and haven't fired since that time. `days` is a list of
    weekday indices (Mon=0..Sun=6); empty = every day. A freshly-armed clock
    trigger is primed to a baseline by the caller, so None isn't expected."""
    now_local = now.astimezone()
    try:
        hour, minute = (int(x) for x in (cfg.get("at") or "").split(":"))
        today_at = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (ValueError, TypeError):
        return False  # missing / malformed / out-of-range time: never fire
    if now_local < today_at:
        return False  # today's time hasn't arrived yet
    days = cfg.get("days") or []
    if days and now_local.weekday() not in days:
        return False  # not one of the chosen weekdays
    if last is None:
        return True  # defensive (caller primes None); fire at/after the time
    return last.astimezone() < today_at  # not yet fired since today's time


def _schedule_label(cfg):
    """Short human label for the fire log line."""
    if (cfg.get("schedule_kind") or "interval").strip() == "clock":
        at = cfg.get("at") or "?"
        days = cfg.get("days") or []
        if days:
            names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            picked = " ".join(names[d] for d in days if 0 <= d < 7)
            return f"at {at} on {picked}"
        return f"daily at {at}"
    return f"every {_interval_minutes(cfg)}m"


def _phase_eval_triggers():
    from planet_maiko.database import db
    from planet_maiko.models.workflow import Workflow
    from planet_maiko.models.pupdate import Pupdate
    from planet_maiko import flows

    started = 0
    try:
        wfs = Workflow.query.filter(Workflow.deleted_at.is_(None)).all()
        now = datetime.now(timezone.utc)
        for wf in wfs:
            triggers = [
                n for n in ((wf.graph or {}).get("nodes") or [])
                if n.get("kind") == "trigger"
            ]
            if not triggers:
                continue
            if not wf.trigger_armed:
                continue  # paused: saved but inert (arm it to go live)

            schedule_triggers = [
                t for t in triggers
                if (t.get("config") or {}).get("trigger_kind") == "schedule"
            ]
            pupdate_triggers = [
                t for t in triggers
                if (t.get("config") or {}).get("trigger_kind", "pupdate") != "schedule"
            ]

            # Schedule triggers fire on a cadence. Two kinds (config.
            # schedule_kind): "interval" (every N min/hours/days, fires once on
            # the first eval after arming then every interval) and "clock" (a
            # wall-clock HH:MM, local, on optional weekdays). One last-fired
            # stamp per flow; the common case is a single schedule trigger.
            if schedule_triggers:
                last = wf.trigger_last_fired_at
                if last is not None and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                for t in schedule_triggers:
                    cfg = t.get("config") or {}
                    is_clock = (cfg.get("schedule_kind") or "interval").strip() == "clock"
                    # A freshly-armed clock trigger primes its baseline to now,
                    # so it fires at the NEXT occurrence of HH:MM rather than
                    # immediately for a time that already passed today.
                    if is_clock and last is None:
                        wf.trigger_last_fired_at = now
                        last = now
                        continue
                    if _schedule_due(cfg, last, now):
                        run = flows.start_run(
                            wf,
                            input=(cfg.get("input") or "Scheduled run").strip() or "Scheduled run",
                            scope_repo=(cfg.get("repo") or "").strip() or None,
                            triggering_pupdate_id=None,
                        )
                        if run:
                            wf.trigger_last_fired_at = now
                            last = now
                            started += 1
                            logger.info(
                                f"[cycle] schedule trigger: flow '{wf.name}' fired "
                                f"({_schedule_label(cfg)})"
                            )

            # Pupdate triggers: fire on new pupdates after the watermark.
            if not pupdate_triggers:
                continue
            watermark = wf.trigger_evaluated_at
            if watermark is None:
                # First sight of this armed flow: consume the backlog silently
                # (set the watermark to now, fire nothing from history).
                wf.trigger_evaluated_at = now
                continue
            new_pupdates = (
                Pupdate.query
                .filter(Pupdate.timestamp > watermark)
                .filter(Pupdate.dismissed == False)  # noqa: E712
                .order_by(Pupdate.timestamp.asc())
                .limit(200)
                .all()
            )
            wf_started = 0
            for p in new_pupdates:
                if not any(_matches(p, t.get("config") or {}) for t in pupdate_triggers):
                    continue
                if wf_started >= _MAX_STARTS_PER_FLOW:
                    logger.info(
                        f"[cycle] trigger cap ({_MAX_STARTS_PER_FLOW}) hit for "
                        f"flow '{wf.name}'; dropping overflow match {p.id}"
                    )
                    continue
                run = flows.start_run(
                    wf,
                    input=_pupdate_input(p),
                    scope_repo=_pupdate_repo(p),
                    triggering_pupdate_id=p.id,
                )
                if run:
                    wf_started += 1
                    started += 1
                    logger.info(
                        f"[cycle] trigger: flow '{wf.name}' fired on pupdate "
                        f"{p.id} ({p.type})"
                    )
            wf.trigger_evaluated_at = now
        db.session.commit()
        return {"started": started}
    except Exception as e:
        logger.warning(f"[cycle] eval triggers skipped: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return {"started": started, "error": str(e)}
