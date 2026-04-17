"""Tests for BasePoller.run() dedup — specifically, that dismissed
pupdates stay dismissed across polls and server restarts.

The old code resurrected any actionable dismissed pupdate that didn't
have a linked Task, which meant "dismiss it" wasn't actually durable
against the next 5-minute poll — dismissals that didn't produce a task
(user responded to the PR on GitHub directly, user didn't want to
triage it into Maiko, etc.) kept bouncing back. Now dismissal sticks;
pollers that want a re-fire bake the changing bit (SHA, updatedAt)
into source_id.
"""

from datetime import datetime, timedelta, timezone

import pytest

from planet_maiko.database import db
from planet_maiko.models.pupdate import Pupdate
from planet_maiko.pollers.base import BasePoller


class StubPoller(BasePoller):
    """Minimal poller that emits whatever we hand it in __init__."""

    def __init__(self, pupdate_dicts):
        self._pupdates = pupdate_dicts

    @property
    def name(self):
        return "stub"

    def poll(self, config):
        return {"pupdates": self._pupdates}

    def to_pupdates(self, raw_data):
        return raw_data.get("pupdates", [])


def _pd(source_id="review/org/repo#42", actionable=True, type_="pr_review_requested"):
    return {
        "source_id": source_id,
        "type": type_,
        "priority": "high",
        "title": "Review this",
        "body": "body",
        "actionable": actionable,
    }


def test_first_poll_creates_pupdate(app):
    with app.app_context():
        poller = StubPoller([_pd()])
        created = poller.run({}, db.session)
        assert created == 1
        assert Pupdate.query.count() == 1


def test_same_source_id_on_second_poll_does_not_duplicate(app):
    """Same source_id hashes to the same id, which makes dedup
    trivial when the pupdate is still active."""
    with app.app_context():
        poller = StubPoller([_pd()])
        poller.run({}, db.session)
        created_second = poller.run({}, db.session)
        assert created_second == 0
        assert Pupdate.query.count() == 1


def test_dismissed_pupdate_stays_dismissed_across_polls(app):
    """The core regression: user dismisses a pupdate, next poll sees
    the same source, and the pupdate must NOT come back. This held
    even when the pupdate was actionable and had no linked task — the
    old code resurrected exactly this case every 5 min and on every
    server restart.
    """
    with app.app_context():
        poller = StubPoller([_pd()])
        poller.run({}, db.session)

        pup = Pupdate.query.first()
        pup.dismissed = True
        pup.dismissed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()

        # Second poll — source is still asserting the same thing
        created_second = poller.run({}, db.session)
        assert created_second == 0

        # Pupdate is still dismissed
        pup = Pupdate.query.first()
        assert pup.dismissed is True, "dismissal must survive the second poll"


def test_source_re_assertion_with_new_source_id_does_create_fresh_pupdate(app):
    """Pollers that want to re-notify on a genuinely new event (e.g.
    GitHub rolling headRefOid into the source_id when a new commit
    lands) keep working — the new source_id hashes differently, so
    a fresh pupdate is created even if the old one was dismissed.
    """
    with app.app_context():
        poller_v1 = StubPoller([_pd(source_id="review/org/repo#42@abc")])
        poller_v1.run({}, db.session)

        pup = Pupdate.query.first()
        pup.dismissed = True
        pup.dismissed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()

        # New commit pushed → SHA in source_id changes → fresh pupdate
        poller_v2 = StubPoller([_pd(source_id="review/org/repo#42@def")])
        created_second = poller_v2.run({}, db.session)
        assert created_second == 1
        assert Pupdate.query.count() == 2


def test_non_actionable_dismissed_pupdate_also_stays_dismissed(app):
    """Non-actionable pupdates never resurrected before either — keep
    that behavior. Both branches collapse to the same "skip" now.
    """
    with app.app_context():
        poller = StubPoller([_pd(actionable=False)])
        poller.run({}, db.session)

        pup = Pupdate.query.first()
        pup.dismissed = True
        pup.dismissed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.session.commit()

        poller.run({}, db.session)
        assert Pupdate.query.first().dismissed is True
