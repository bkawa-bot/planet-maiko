"""PagerDuty poller — fetches incidents assigned to the on-call user.

Generates pupdates for:
    - Incidents currently triggered or acknowledged and assigned to you.

Resolved incidents aren't emitted — they're handled by the dedup path
in base.py (a resolved incident simply stops re-appearing in the poll
response, so its pupdate stops refreshing). If you need "incident was
just resolved" notifications, that becomes a future type; the current
scope is "what needs me right now."
"""

import logging

from planet_maiko.pollers.base import BasePoller
from planet_maiko.pollers.pagerduty_client import PagerDutyClient

logger = logging.getLogger(__name__)

# Incident.urgency is "high" or "low" — map to Maiko's priority bucket.
# "high" urgency is the paging-everyone-now kind; "low" is usually a
# secondary alert that still wants a look but not a fire drill.
URGENCY_TO_PRIORITY = {"high": "urgent", "low": "normal"}


class PagerDutyPoller(BasePoller):
    """Poll PagerDuty for incidents assigned to the authenticated user."""

    @property
    def name(self):
        return "pagerduty"

    def poll(self, config):
        api_token = config.get("api_token", "")
        if not api_token:
            logger.warning("[pagerduty] No API token configured, skipping poll")
            return {"incidents": []}

        client = PagerDutyClient(api_token=api_token)

        # Resolve "me" once per poll — user_id rarely changes but
        # caching in the config blob would require a save path. One
        # extra GET is cheap compared to the /incidents call.
        try:
            me = client.fetch_me()
        except Exception as e:
            logger.warning(f"[pagerduty] fetch_me failed: {e}")
            return {"incidents": []}

        user_id = me.get("id")
        if not user_id:
            logger.warning("[pagerduty] /users/me returned no id, skipping poll")
            return {"incidents": []}

        try:
            incidents = client.fetch_assigned_incidents(user_id)
        except Exception as e:
            logger.warning(f"[pagerduty] fetch_assigned_incidents failed: {e}")
            return {"incidents": []}

        return {"incidents": incidents, "user_id": user_id}

    def to_pupdates(self, raw_data):
        pupdates = []
        for incident in raw_data.get("incidents", []):
            incident_id = incident.get("id")
            if not incident_id:
                continue

            number = incident.get("incident_number")
            title = incident.get("title", "")
            status = (incident.get("status") or "").lower()
            urgency = (incident.get("urgency") or "low").lower()
            service = (incident.get("service") or {}).get("summary") or ""
            priority_obj = incident.get("priority") or {}
            priority_label = priority_obj.get("summary") or ""

            # "acknowledged" is one rung down from "triggered" — someone
            # (you) owns it but it's not resolved yet. Still worth a
            # pupdate, but one step less urgent than an untouched page.
            maiko_priority = URGENCY_TO_PRIORITY.get(urgency, "normal")
            if status == "acknowledged" and maiko_priority == "urgent":
                maiko_priority = "high"

            display_title = (
                f"#{number}: {title}" if number is not None else title
            )

            tags = [status, f"urgency:{urgency}"]
            if service:
                tags.append(service)
            if priority_label:
                tags.append(f"priority:{priority_label}")

            pupdates.append({
                # source_id includes status so a triggered→acknowledged
                # transition emits a fresh pupdate (the base's dedup
                # hashes source_id). If we dedupe on plain id, the
                # status change would be invisible to the feed.
                "source_id": f"{incident_id}/{status}",
                "type": "pagerduty_incident",
                "priority": maiko_priority,
                "title": display_title,
                "body": incident.get("description") or f"Status: {status}",
                "url": incident.get("html_url") or "",
                "actionable": True,
                "action_hint": "Acknowledge or resolve",
                "tags": tags,
                "metadata": {
                    "incident_id": incident_id,
                    "incident_number": number,
                    "status": status,
                    "urgency": urgency,
                    "service": service,
                    "priority_label": priority_label,
                    "created_at": incident.get("created_at"),
                    "last_status_change_at": incident.get("last_status_change_at"),
                },
            })

        return pupdates
