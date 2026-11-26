from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient

from billing.api import create_app
from billing.config import Settings
from billing.notify import TIMEOUT_SECONDS, Gateway


class FakeResponse:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code, self.body = status, body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} from the gateway")

    def json(self) -> dict[str, Any]:
        return self.body


class FakeSession(requests.Session):
    """Records each post; answers with the next canned response, or raises it if it is an exception."""

    def __init__(self, *answers: FakeResponse | Exception) -> None:
        super().__init__()
        self.answers = list(answers)
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> Any:  # type: ignore[override]
        self.posts.append({"url": url, **kwargs})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_gateway_posts_the_message() -> None:
    session = FakeSession(FakeResponse(202, {"message_id": "m-1"}))
    gateway = Gateway("https://notify.example/messages", session)
    assert gateway.send({"kind": "payment_reminder"}) == "m-1"
    assert session.posts == [{"url": "https://notify.example/messages", "json": {"kind": "payment_reminder"},
                              "timeout": TIMEOUT_SECONDS}]
    assert session.headers["User-Agent"] == "harbor-billing-service"


def test_gateway_raises_on_an_error_status() -> None:
    gateway = Gateway("https://notify.example/messages", FakeSession(FakeResponse(503, {})))
    with pytest.raises(requests.HTTPError):
        gateway.send({})


def test_gateway_has_its_own_session_by_default() -> None:
    assert isinstance(Gateway("https://notify.example/messages").session, requests.Session)


def test_sends_the_reminders_due_that_day(client: TestClient) -> None:
    session = FakeSession(FakeResponse(202, {"message_id": "m-1"}))
    client.app.state.gateway = Gateway("https://notify.example/messages", session)
    body = client.post("/reminders/send", params={"on": "2026-11-23"}).json()
    assert body == {"on": "2026-11-23", "sent": [{"policy_id": "P-5001", "installment": 11, "message_id": "m-1"}],
                    "failed": []}
    assert session.posts[0]["json"] == {
        "kind": "payment_reminder", "channel": "email", "customer_id": "C-1001", "name": "Asha Rao",
        "policy_id": "P-5001", "installment": 11, "due_date": "2026-11-30", "amount_due": "120.63",
    }


def test_a_failed_delivery_is_reported_not_raised(client: TestClient) -> None:
    client.app.state.gateway = Gateway("https://notify.example/messages",
                                       FakeSession(requests.ConnectionError("gateway down")))
    body = client.post("/reminders/send", params={"on": "2026-11-13"}).json()
    assert body == {"on": "2026-11-13", "sent": [],
                    "failed": [{"policy_id": "P-5004", "installment": 4, "error": "ConnectionError"}]}


def test_mail_channel_and_default_day(client: TestClient) -> None:
    session = FakeSession(FakeResponse(202, {"message_id": "m-2"}))
    client.app.state.gateway = Gateway("https://notify.example/messages", session)
    assert client.post("/reminders/send").json() == {"on": "2026-11-03", "sent": [], "failed": []}
    client.post("/reminders/send", params={"on": "2026-11-13"})
    assert session.posts[0]["json"]["channel"] == "mail"


def test_not_configured(client: TestClient) -> None:
    response = client.post("/reminders/send")
    assert response.status_code == 409
    assert response.json()["detail"] == "reminder delivery is not configured: set BILLING_NOTIFY_URL"


def test_gateway_from_settings(settings: Settings) -> None:
    app = create_app(replace(settings, notify_url="https://notify.example/messages"))
    assert app.state.gateway.url == "https://notify.example/messages"
