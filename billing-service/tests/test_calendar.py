from fastapi.testclient import TestClient


def test_holidays_2026(client: TestClient) -> None:
    body = client.get("/calendar/holidays", params={"year": 2026}).json()
    assert body["year"] == 2026
    assert body["holidays"][0] == {"date": "2026-01-01", "name": "New Year's Day"}
    dates = {h["name"]: h["date"] for h in body["holidays"]}
    assert dates["Independence Day"] == "2026-07-03"  # Saturday, observed Friday
    assert dates["Memorial Day"] == "2026-05-25"
    assert dates["Thanksgiving Day"] == "2026-11-26"
    assert len(body["holidays"]) == 11


def test_holidays_2027(client: TestClient) -> None:
    dates = {h["name"]: h["date"] for h in client.get("/calendar/holidays", params={"year": 2027}).json()["holidays"]}
    assert dates["Juneteenth National Independence Day"] == "2027-06-18"  # Saturday, observed Friday
    assert dates["Independence Day"] == "2027-07-05"  # Sunday, observed Monday
    assert dates["Christmas Day"] == "2027-12-24"


def test_year_out_of_range(client: TestClient) -> None:
    assert client.get("/calendar/holidays", params={"year": 1999}).status_code == 422
