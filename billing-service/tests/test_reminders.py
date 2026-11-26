from datetime import date

import pytest
from fastapi.testclient import TestClient

from billing.reminders import reminder_date


@pytest.mark.parametrize(
    ("due", "expected"),
    [
        (date(2026, 11, 30), date(2026, 11, 23)),
        (date(2026, 12, 31), date(2026, 12, 24)),
        (date(2026, 12, 3), date(2026, 11, 26)),
        (date(2027, 3, 1), date(2027, 2, 22)),
        (date(2028, 3, 7), date(2028, 2, 29)),
    ],
)
def test_reminder_a_week_before(due: date, expected: date) -> None:
    assert reminder_date(due) == expected


def test_reminders_for_unpaid_installments(client: TestClient) -> None:
    body = client.get("/policies/P-5001/reminders").json()
    assert body == {"policy_id": "P-5001", "reminders": [
        {"installment": 11, "due_date": "2026-11-30", "remind_on": "2026-11-23", "status": "scheduled"},
        {"installment": 12, "due_date": "2026-12-31", "remind_on": "2026-12-24", "status": "scheduled"},
    ]}
    overdue = client.get("/policies/P-5005/reminders").json()["reminders"]
    assert overdue == [{"installment": 2, "due_date": "2026-11-01", "remind_on": "2026-10-25", "status": "sent"}]


def test_no_reminders_when_paid_up_or_cancelled(client: TestClient) -> None:
    assert client.get("/policies/P-5003/reminders").json()["reminders"] == []
    client.post("/policies/P-5001/cancel", json={"cancel_date": "2026-11-03"})
    assert client.get("/policies/P-5001/reminders").json()["reminders"] == []
    assert client.get("/policies/P-9999/reminders").status_code == 404
