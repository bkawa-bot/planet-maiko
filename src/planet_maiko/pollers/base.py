import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class BasePoller(ABC):
    """Base class for all integration pollers.

    Required (subclasses must implement):
        - name: unique string identifier (e.g. "github", "jira")
        - poll(): fetch raw data from the external source
        - to_pupdates(): transform raw data into pupdate dicts

    Optional (subclasses can override):
        - to_signals(): extract feedback signals for the learning system
        - get_rules(): return brain rules for this integration
        - get_categories(): return signal categories this plugin uses

    The base class handles dedup (via source_id) and database insertion.
    """

    @property
    @abstractmethod
    def name(self):
        """Unique name for this poller (e.g. 'github', 'linear')."""
        ...

    @abstractmethod
    def poll(self, config):
        """Fetch raw data from the external source.

        Args:
            config: dict of integration-specific config

        Returns:
            Raw data (any format - passed to to_pupdates and to_signals)
        """
        ...

    @abstractmethod
    def to_pupdates(self, raw_data):
        """Transform raw data into a list of pupdate dicts.

        Each dict should have at minimum:
            - source_id: str (unique key for dedup)
            - type: str (e.g. "pr_review_requested", "jira_assigned")
            - title: str
            - priority: str ("low", "normal", "high", "urgent")

        Optional fields:
            - body, url, actionable, action_hint, tags, metadata, expires_at

        Returns:
            list of pupdate dicts
        """
        ...

    def to_signals(self, raw_data):
        """Extract feedback signals for the learning system.

        Override this to define what constitutes positive/negative
        feedback in your domain. Each signal dict should have:
            - text: str (the feedback content)
            - category: str (e.g. "testing", "prioritization", "design_feedback")
            - source_type: str (e.g. "pr_comment", "ticket_resolution")
            - type: str ("positive" or "negative") - optional, for outcome tracking

        Optional fields:
            - reviewer: str (who gave the feedback)
            - repo: str (what repo/project it's about)
            - severity: str ("suggestion", "warning", "blocking")

        Returns:
            list of signal dicts (default: empty)
        """
        return []

    def get_rules(self):
        """Return brain rules for this integration.

        Override to provide default triage rules for your pupdates.
        These get merged with the global rules on startup.

        Each rule dict should have:
            - name: str (unique rule name)
            - description: str
            - match: dict (conditions to match pupdates)
            - action: str ("dismiss", "mark_read", "create_task", "skip")

        Optional:
            - task_type: str (if action is "create_task")
            - task_priority: str (if action is "create_task")

        Returns:
            list of rule dicts (default: empty)
        """
        return []

    def get_categories(self):
        """Return signal categories this plugin uses.

        Override to register new learning categories beyond the
        built-in ones (null_safety, testing, etc.).

        Returns:
            list of category name strings (default: empty)
        """
        return []

    def generate_id(self, source_id):
        """Generate a deterministic pupdate ID from the source_id."""
        raw = f"{self.name}:{source_id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def run(self, config, db_session):
        """Execute a full poll cycle: fetch, transform, dedup, insert.

        Handles pupdates AND signals in one pass.

        Args:
            config: integration config dict
            db_session: SQLAlchemy session

        Returns:
            int: number of new pupdates created
        """
        from planet_maiko.models.pupdate import Pupdate

        try:
            raw_data = self.poll(config)
            pupdate_dicts = self.to_pupdates(raw_data)
        except Exception as e:
            logger.error(f"[{self.name}] Poll failed: {e}")
            return 0

        created = 0
        resurrected = 0
        for pd in pupdate_dicts:
            pupdate_id = self.generate_id(pd["source_id"])

            existing = db_session.get(Pupdate, pupdate_id)
            if existing:
                # Active duplicate — skip
                if not existing.dismissed:
                    continue
                # Dismissed but the source is asserting it again. For
                # actionable items (review re-requests, new CI
                # failures on the same PR, etc.) this is a real "hey,
                # please look again" — resurrect the pupdate instead
                # of silently dropping it. Non-actionable status
                # pupdates (PR is open, PR is approved) stay
                # dismissed so they don't keep popping back.
                if not pd.get("actionable", False):
                    continue

                # Don't resurrect when the user has already triaged
                # this pupdate via a Task — the task is in their list
                # and resurrecting just clutters the inbox with a
                # duplicate signal. Re-events on the *same* source_id
                # (PR re-requested, same Linear issue re-asserted) are
                # the responsibility of the poller to disambiguate
                # (include event id / timestamp in source_id) — the
                # base layer can't tell "user wants to know again"
                # from "this is the same notification I already saw".
                from planet_maiko.models.task import Task as _Task
                related_task = _Task.query.filter_by(
                    source_pupdate_id=pupdate_id
                ).first()
                if related_task is not None:
                    continue

                # No task ever existed for this pupdate — the user
                # really did just dismiss it. Resurrect so they see
                # the re-asserted source. Re-fire the rule too so a
                # task gets created if the rule decides one is needed.
                existing.dismissed = False
                existing.dismissed_at = None
                existing.read = False
                existing.brain_processed = False
                existing.title = pd["title"]
                existing.body = pd.get("body")
                existing.priority = pd.get("priority", existing.priority)
                existing.url = pd.get("url") or existing.url
                existing.action_hint = pd.get("action_hint") or existing.action_hint
                existing.tags = pd.get("tags", existing.tags or [])
                existing.extra = pd.get("metadata", existing.extra or {})
                existing.timestamp = datetime.now(timezone.utc)
                resurrected += 1
                continue

            pupdate = Pupdate(
                id=pupdate_id,
                timestamp=datetime.now(timezone.utc),
                source=self.name,
                source_id=pd["source_id"],
                type=pd["type"],
                priority=pd.get("priority", "normal"),
                title=pd["title"],
                body=pd.get("body"),
                url=pd.get("url"),
                actionable=pd.get("actionable", False),
                action_hint=pd.get("action_hint"),
                tags=pd.get("tags", []),
                extra=pd.get("metadata", {}),
            )
            if pd.get("expires_at"):
                pupdate.expires_at = datetime.fromisoformat(pd["expires_at"])

            db_session.add(pupdate)
            created += 1

        # Also extract and store signals
        try:
            signal_dicts = self.to_signals(raw_data)
            if signal_dicts:
                from planet_maiko.models.signal import Signal
                new_signals = 0
                for s in signal_dicts:
                    signal = Signal(
                        category=s.get("category", "domain_knowledge"),
                        text=s["text"][:500],
                        source_type=s.get("source_type", self.name),
                        reviewer=s.get("reviewer"),
                        severity=s.get("severity", "suggestion"),
                        repo=s.get("repo"),
                        language=s.get("language"),
                        file_path=s.get("file_path"),
                        # Pollers set real categories directly, no LLM
                        # synthesis needed.
                        synthesized=True,
                    )
                    db_session.add(signal)
                    new_signals += 1
                if new_signals:
                    logger.info(f"[{self.name}] Created {new_signals} learning signal(s)")
        except Exception as e:
            logger.debug(f"[{self.name}] Signal extraction skipped: {e}")

        # Subclass hook: per-poller custom processing that needs access
        # to the raw_data AND the live db session. Used by github_poller
        # to scrape inline review comments from newly merged PRs into
        # unsynthesized Signals without duplicating poll scaffolding.
        try:
            self._after_sync(raw_data, db_session)
        except Exception as e:
            logger.warning(f"[{self.name}] _after_sync hook failed: {e}")

        if created or resurrected or signal_dicts:
            db_session.commit()
            if created:
                logger.info(f"[{self.name}] Created {created} new pupdate(s)")
            if resurrected:
                logger.info(f"[{self.name}] Resurrected {resurrected} dismissed pupdate(s)")

        return created + resurrected

    def _after_sync(self, raw_data, db_session):
        """Hook called after pupdates + signals have been staged but
        before commit. Default no-op; subclasses override for custom
        post-poll work (e.g. per-PR API calls).
        """
        pass
