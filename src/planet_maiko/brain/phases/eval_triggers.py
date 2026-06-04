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

            # Schedule triggers: fire when the interval has elapsed since the
            # last fire (once on the first eval after arming, then every
            # interval). One last-fired stamp per flow; the common case is a
            # single schedule trigger.
            if schedule_triggers:
                last = wf.trigger_last_fired_at
                if last is not None and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                for t in schedule_triggers:
                    cfg = t.get("config") or {}
                    interval_min = _interval_minutes(cfg)
                    if last is None or (now - last).total_seconds() >= interval_min * 60:
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
                                f"[cycle] schedule trigger: flow '{wf.name}' "
                                f"fired (every {interval_min}m)"
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
