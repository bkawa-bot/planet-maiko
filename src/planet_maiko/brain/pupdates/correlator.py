"""Correlator - groups related pupdates into incident clusters.

Looks for patterns like CI failure + deploy rollback + error spike
within a time window and creates a single "incident" pupdate.
"""

import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate

logger = logging.getLogger(__name__)

CORRELATION_WINDOW_MINUTES = 30

# Known cause chains (incident patterns)
CAUSE_CHAINS = [
    ["pr_ci_failed", "deploy_rollback", "error_spike"],
    ["deploy_blocked", "deploy_stuck"],
    ["pr_ci_failed", "deploy_blocked"],
    ["deploy_rollback", "error_spike"],
    ["batch_job_failing", "error_spike"],
    ["deploy_rollback", "batch_job_failing"],
]

# Types to skip (internal, not real events)
SKIP_TYPES = {"eod_signal", "agent_learnings", "agent_update", "agent_done", "agent_ready"}


def correlate():
    """Scan recent pupdates for incident patterns.

    Returns:
        dict with count of incidents created
    """
    window_start = datetime.now(timezone.utc) - timedelta(minutes=CORRELATION_WINDOW_MINUTES)

    recent = (
        Pupdate.query
        .filter(
            Pupdate.timestamp >= window_start,
            Pupdate.dismissed == False,
            Pupdate.type.notin_(SKIP_TYPES),
        )
        .order_by(Pupdate.timestamp.asc())
        .all()
    )

    if len(recent) < 2:
        return {"incidents_created": 0}

    # Group by service/repo
    by_service = defaultdict(list)
    for p in recent:
        service = _extract_service(p)
        if service:
            by_service[service].append(p)

    incidents_created = 0

    for service, pupdates in by_service.items():
        if len(pupdates) < 2:
            continue

        types_present = {p.type for p in pupdates}

        # Check each cause chain
        for chain in CAUSE_CHAINS:
            matching = types_present.intersection(chain)
            if len(matching) >= 2:
                # Check we haven't already created this incident
                incident_id = f"incident-{service}-{'-'.join(sorted(matching))}"
                existing = db.session.get(Pupdate, incident_id[:64])
                if existing:
                    continue

                # Create incident pupdate
                highest_priority = _highest_priority([p for p in pupdates if p.type in matching])
                correlated = [p for p in pupdates if p.type in matching]
                body_lines = [f"Correlated {len(correlated)} events for {service}:"]
                for p in correlated:
                    body_lines.append(f"- [{p.priority}] {p.type}: {p.title}")
                body_lines.append(f"\nPattern: {' → '.join(sorted(matching))}")

                incident = Pupdate(
                    id=incident_id[:64],
                    source="maiko",
                    source_id=f"incident/{service}",
                    type="incident",
                    priority=highest_priority,
                    title=f"Incident: {service} ({', '.join(sorted(matching))})",
                    body="\n".join(body_lines),
                    actionable=True,
                    action_hint="Investigate incident",
                    tags=[service, "incident"],
                    extra={
                        "correlated_ids": [p.id for p in correlated],
                        "services": [service],
                        "pattern": sorted(list(matching)),
                        "types": sorted(list(matching)),
                    },
                )
                db.session.add(incident)

                # Mark originals as read
                for p in correlated:
                    p.read = True

                incidents_created += 1
                logger.info(f"[correlator] Created incident for {service}: {sorted(matching)}")

    if incidents_created:
        db.session.commit()

    return {"incidents_created": incidents_created}


def _extract_service(pupdate):
    """Extract service/repo name from a pupdate."""
    extra = pupdate.extra or {}
    if extra.get("repo"):
        return extra["repo"]
    tags = pupdate.tags or []
    for tag in tags:
        if "/" in tag:  # org/repo format
            return tag
    return tags[0] if tags else None


def _highest_priority(pupdates):
    """Get the highest priority among a list of pupdates."""
    order = {"critical": 0, "urgent": 1, "high": 2, "normal": 3, "low": 4}
    return min(pupdates, key=lambda p: order.get(p.priority, 99)).priority
