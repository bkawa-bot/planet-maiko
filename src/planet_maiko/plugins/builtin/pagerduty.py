"""PagerDuty plugin. Fetches incidents assigned to the on-call user.

Generates pupdates for incidents currently triggered or acknowledged and
assigned to you. Resolved incidents drop out naturally (no row in the
poll response means no dedup refresh).
"""

import logging

from planet_maiko.integrations.clients.pagerduty_client import PagerDutyClient
from planet_maiko.plugins.helpers import PollerPlugin

logger = logging.getLogger(__name__)

# Incident.urgency is "high" or "low". Map to Maiko's priority bucket.
URGENCY_TO_PRIORITY = {"high": "urgent", "low": "normal"}


class PagerDutyPlugin(PollerPlugin):
    name = "pagerduty"

    def get_config_defaults(self):
        return {"pagerduty": {"enabled": False, "poll_interval_minutes": 5, "api_token": ""}}

    def poll(self, config):
        api_token = config.get("api_token", "")
        if not api_token:
            logger.warning("[pagerduty] No API token configured, skipping poll")
            return {"incidents": []}

        client = PagerDutyClient(api_token=api_token)

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

            # "acknowledged" is one rung down from "triggered". Someone
            # owns it but it's not resolved yet.
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
                # source_id includes status so a triggered->acknowledged
                # transition emits a fresh pupdate. Plain id would hide
                # the status change from the feed.
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
