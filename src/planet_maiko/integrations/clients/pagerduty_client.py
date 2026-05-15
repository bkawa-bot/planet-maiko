"""Shared PagerDuty REST client.

Centralizes auth, error handling, and request dispatch so the poller +
one-off settings calls go through the same place. Mirrors the Linear
client shape — not a singleton; construct with an explicit token
override in tests, default constructor pulls from
config.pagerduty.api_token.
"""

import logging
import requests

from planet_maiko.config import load_config

logger = logging.getLogger(__name__)

PAGERDUTY_API = "https://api.pagerduty.com"


class PagerDutyClient:
    """Thin REST wrapper over the PagerDuty API v2."""

    def __init__(self, api_token=None):
        self.api_token = api_token or self._load_token()
        if not self.api_token:
            raise ValueError("PagerDuty API token not configured")

    @staticmethod
    def _load_token():
        return (load_config().get("pagerduty") or {}).get("api_token") or None

    def _headers(self):
        return {
            "Authorization": f"Token token={self.api_token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
            "Content-Type": "application/json",
        }

    def get(self, path, params=None, timeout=30):
        """GET <path> and return the parsed JSON body.

        Raises requests.HTTPError on non-2xx. PagerDuty returns useful
        error bodies for 400/401/403 — the caller is expected to wrap
        with try/except and surface err.response.text if needed.
        """
        import certifi

        url = path if path.startswith("http") else f"{PAGERDUTY_API}{path}"
        resp = requests.get(
            url,
            headers=self._headers(),
            params=params,
            timeout=timeout,
            verify=certifi.where(),
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_me(self):
        """Return the authenticated user object.

        Used both by the Settings test-connection button and by the
        poller to resolve `user_ids[]` before fetching incidents.
        """
        data = self.get("/users/me")
        return data.get("user") or {}

    def fetch_assigned_incidents(self, user_id, statuses=None, limit=100):
        """Incidents assigned to the given user with the given statuses.

        Defaults to open statuses (triggered + acknowledged) — resolved
        incidents aren't useful as pupdates. Capped at 100 per response
        (PD's max) without pagination; bigger on-call loads can add
        offset-based paging later, but 100 open incidents already means
        the wheel is on fire.
        """
        if statuses is None:
            statuses = ["triggered", "acknowledged"]
        params = [
            ("user_ids[]", user_id),
            ("limit", str(min(limit, 100))),
        ]
        for s in statuses:
            params.append(("statuses[]", s))
        data = self.get("/incidents", params=params)
        return data.get("incidents") or []
