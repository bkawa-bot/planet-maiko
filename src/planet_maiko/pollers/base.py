import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class BasePoller(ABC):
    """Base class for all integration pollers.

    Subclasses implement:
        - name: unique string identifier (e.g. "github")
        - poll(): fetch raw data from the external source
        - to_pupdates(): transform raw data into pupdate dicts

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
            Raw data (any format - passed to to_pupdates)
        """
        ...

    @abstractmethod
    def to_pupdates(self, raw_data):
        """Transform raw data into a list of pupdate dicts.

        Each dict should have at minimum:
            - source: str (same as self.name)
            - source_id: str (unique key for dedup)
            - type: str (e.g. "pr_review_requested")
            - title: str
            - priority: str ("low", "normal", "high", "urgent")

        Optional fields:
            - body, url, actionable, action_hint, tags, metadata, expires_at

        Returns:
            list of pupdate dicts
        """
        ...

    def generate_id(self, source_id):
        """Generate a deterministic pupdate ID from the source_id."""
        raw = f"{self.name}:{source_id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def run(self, config, db_session):
        """Execute a full poll cycle: fetch, transform, dedup, insert.

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

        if created:
            db_session.commit()
            logger.info(f"[{self.name}] Created {created} new pupdate(s)")

        return created
