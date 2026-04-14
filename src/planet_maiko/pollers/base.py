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
        for pd in pupdate_dicts:
            pupdate_id = self.generate_id(pd["source_id"])

            # Skip if we already have this pupdate
            existing = db_session.get(Pupdate, pupdate_id)
            if existing:
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

        if created or signal_dicts:
            db_session.commit()
            if created:
                logger.info(f"[{self.name}] Created {created} new pupdate(s)")

        return created
