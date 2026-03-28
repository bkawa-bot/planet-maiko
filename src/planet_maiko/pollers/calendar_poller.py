"""Calendar poller - fetches events from iCal feeds.

Works with any standard iCal URL:
    - Google Calendar (Settings > Calendar > Secret address in iCal format)
    - Outlook/Office 365 (Publish calendar > ICS link)
    - Any CalDAV server

Generates pupdates for:
    - Upcoming events today
    - 1:1 meetings (2 attendees) with prep hints
"""

import logging
from datetime import datetime, timezone, timedelta

import requests
from icalendar import Calendar

from planet_maiko.pollers.base import BasePoller

logger = logging.getLogger(__name__)


class CalendarPoller(BasePoller):

    @property
    def name(self):
        return "calendar"

    def poll(self, config):
        ical_urls = config.get("ical_urls", [])
        if not ical_urls:
            logger.warning("[calendar] No iCal URLs configured, skipping poll")
            return {"events": []}

        all_events = []
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
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

                    # Handle date vs datetime
                    if not isinstance(dt, datetime):
                        dt = datetime.combine(dt, datetime.min.time(), tzinfo=timezone.utc)
                    elif dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)

                    # Only include today's events
                    if not (today_start <= dt < today_end):
                        continue

                    summary = str(component.get("summary", "Untitled event"))
                    location = str(component.get("location", "")) if component.get("location") else ""
                    description = str(component.get("description", "")) if component.get("description") else ""
                    attendees = component.get("attendee")

                    # Normalize attendees to a list
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

        # Sort by start time
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

            # Build body
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
