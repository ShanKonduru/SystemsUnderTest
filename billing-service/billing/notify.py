"""Reminder delivery (BR-18): each reminder due today is posted to the notification gateway at BILLING_NOTIFY_URL,
which sends it by email (paperless customers) or by mail."""

from __future__ import annotations

from typing import Any

import requests

TIMEOUT_SECONDS = 10


class Gateway:
    def __init__(self, url: str, session: requests.Session | None = None) -> None:
        self.url = url
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = "harbor-billing-service"

    def send(self, message: dict[str, Any]) -> str:
        """Posts one message and returns the gateway's message id. Raises requests.RequestException on failure."""
        response = self.session.post(self.url, json=message, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return str(response.json()["message_id"])
