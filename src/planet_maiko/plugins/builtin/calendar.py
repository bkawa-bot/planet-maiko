"""Calendar plugin. Fetches today's events from iCal feeds.

Works with any standard iCal URL (Google Calendar, Outlook, CalDAV).

Generates pupdates for:
    - Upcoming events today
    - 1:1 meetings (2 attendees) with prep hints
"""

import logging
from datetime import datetime, timedelta

import requests
from icalendar import Calendar

from planet_maiko.plugins.poller import PollerPlugin

logger = logging.getLogger(__name__)


class CalendarPlugin(PollerPlugin):
    name = "calendar"

    def get_config_defaults(self):
        return {"calendar": {"enabled": False, "poll_interval_minutes": 30, "ical_urls": []}}

    def get_config_schema(self):
        return {
            "enabled": {"type": "bool", "label": "Enabled"},
            "ical_urls": {
                "type": "list", "label": "iCal URLs",
                "placeholder": "https://calendar.google.com/...",
                "help": "iCal/ICS URLs. Google: Settings → your calendar → Secret address in iCal format.",
            },
            "poll_interval_minutes": {
                "type": "number", "label": "Poll interval (minutes)",
            },
        }

    def poll(self, config):
        ical_urls = config.get("ical_urls", [])
        if not ical_urls:
            logger.warning("[calendar] No iCal URLs configured, skipping poll")
            return {"events": []}

        all_events = []
        # Use the user's timezone for "today" so evening meetings don't
        # get filtered out as "tomorrow UTC".
        from planet_maiko.config import user_now
        local_now = user_now()
        today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        for url in ical_urls:
            try:
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                cal = Calendar.from_ical(resp.text)

                for component in cal.walk():
                    if component.name != "VEVENT":
                        continue

                    dtstart = component.get("dtstart")
                    if dtstart is None:
                        continue
                    dt = dtstart.dt

                    if not isinstance(dt, datetime):
                        dt = datetime.combine(dt, datetime.min.time()).astimezone()
                    elif dt.tzinfo is None:
                        dt = dt.astimezone()

                    if not (today_start <= dt < today_end):
                        continue

                    summary = str(component.get("summary", "Untitled event"))
                    location = str(component.get("location", "")) if component.get("location") else ""
                    description = str(component.get("description", "")) if component.get("description") else ""
                    attendees = component.get("attendee")

                    if attendees is None:
                        attendee_list = []
                    elif isinstance(attendees, list):
                        attendee_list = [str(a).replace("mailto:", "") for a in attendees]
                    else:
                        attendee_list = [str(attendees).replace("mailto:", "")]

                    all_events.append({
                        "summary": summary,
                        "start": dt.isoformat(),
                        "location": location,
                        "description": description,
                        "attendees": attendee_list,
                        "uid": str(component.get("uid", "")),
                    })

            except Exception as e:
                logger.error(f"[calendar] Failed to fetch {url}: {e}")

        all_events.sort(key=lambda e: e["start"])
        return {"events": all_events}

    def to_pupdates(self, raw_data):
        pupdates = []

        for event in raw_data.get("events", []):
            summary = event["summary"]
            start = event["start"]
            attendees = event.get("attendees", [])
            location = event.get("location", "")
            is_1on1 = len(attendees) == 2

            body_parts = []
            try:
                dt = datetime.fromisoformat(start)
                body_parts.append(f"Time: {dt.strftime('%I:%M %p')}")
            except (ValueError, TypeError):
                body_parts.append(f"Time: {start}")
            if location:
                body_parts.append(f"Location: {location}")
            if attendees:
                body_parts.append(f"Attendees: {', '.join(attendees[:5])}")

            pupdate_type = "calendar_1on1" if is_1on1 else "calendar_event"
            action_hint = "Prepare for 1:1" if is_1on1 else None

            pupdates.append({
                "source_id": f"event/{event.get('uid', summary)}/{start[:10]}",
                "type": pupdate_type,
                "priority": "normal" if is_1on1 else "low",
                "title": summary,
                "body": "\n".join(body_parts),
                "actionable": is_1on1,
                "action_hint": action_hint,
                "tags": ["1:1"] if is_1on1 else [],
                "metadata": {
                    "start": start,
                    "location": location,
                    "attendee_count": len(attendees),
                },
            })

        return pupdates
