"""The self-healing features: release rules, the v4 release, live drift settings and the locator map."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from automation_lab.app import create_app
from automation_lab.config import Settings, drift_seconds
from automation_lab.locators import camel, locator_map, pascal, rules
from tests.conftest import TOKEN, make_settings

SANDBOX = {"X-Sandbox-Token": TOKEN}


def by_key(release: str) -> dict:
    return {e["key"]: e for e in locator_map(release)["elements"]}


def test_config_carries_rules_and_drift(client: TestClient) -> None:
    cfg = client.get("/api/config").json()
    assert set(cfg["releases"]) == {"v1", "v2", "v3", "v4"}
    assert cfg["rules"]["schemes"] == {"v1": "authored", "v2": "renamed", "v3": "random", "v4": "random"}
    assert cfg["rules"]["text"]["v4"]["Get my quote"] == "See my price"
    assert cfg["drift_seconds"] == 0 and client.get("/healthz").json()["drift_seconds"] == 0


def test_naming_helpers_match_release_js() -> None:
    assert pascal("quote-first-name") == "QuoteFirstName" and pascal("first_name") == "FirstName"
    assert camel("first_name") == "firstName" and camel("product") == "product"
    assert rules()["releases"] == ["v1", "v2", "v3", "v4"]


def test_locator_map_v1_and_v2_are_deterministic(client: TestClient) -> None:
    v1, v2 = by_key("v1"), by_key("v2")
    assert (v1["quote-first-name"]["id"], v1["quote-first-name"]["name"], v1["quote-first-name"]["css"]) == \
        ("quote-first-name", "first_name", "#quote-first-name")
    assert (v2["quote-first-name"]["id"], v2["quote-first-name"]["name"], v2["quote-first-name"]["testid"]) == \
        ("hmQuoteFirstName", "firstName", "quote-first-name")
    assert v1["quote-submit"]["text"] == "Get my quote" and v2["quote-submit"]["text"] == "Calculate premium"
    assert v1["quote-email"]["placeholder"] == "you@example.com" and v2["quote-email"]["placeholder"] == "name@email.com"
    assert v1["quote-first-name"]["role"] == "textbox" and v1["quote-first-name"]["accessible_name"] == "First name"
    assert v1["quote-deductible"]["role"] == "slider" and v1["quote-make"]["role"] == "combobox"
    assert v1["claim-days"]["role"] == "listbox" and v1["policies-search"]["role"] == "searchbox"
    assert v1["page-quote"]["role"] == "region" and v1["page-quote"]["accessible_name"] == "Get a quote"
    assert v1["claim-contact-email"]["accessible_name"] == "Email"   # named by its wrapping label
    assert v1["quote-phone"]["accessible_name"] == "Phone (optional)"
    assert v1["pay-name"]["page"] == "payment-frame" and v1["quote-first-name"]["page"] == "quote"
    assert v1["release-select"]["page"] == "shell"
    assert v1["ix-payment-frame"]["accessible_name"] == "Payment widget" and v2["ix-payment-frame"]["name"] == "payment"
    body = client.get("/api/locator-map", params={"release": "v2"}).json()
    assert body["release"] == "v2" and not body["dynamic_ids"] and body["count"] == len(body["elements"]) > 300
    assert body["structure"]["fields_wrapped"] and ["quote-field-email", "quote-field-phone"] in body["structure"]["swaps"]


def test_locator_map_v3_and_v4_are_dynamic() -> None:
    v3, v4 = by_key("v3"), by_key("v4")
    for e in (v3["quote-first-name"], v4["quote-first-name"]):
        assert e["id"] is None and e["css"] is None and e["name"] is None and e["testid"] is None and e["dynamic"]
    assert v3["quote-first-name"]["accessible_name"] == "First name"
    assert v4["quote-first-name"]["accessible_name"] == "Given name"
    assert v4["quote-submit"]["text"] == "See my price" and v4["quote-email"]["placeholder"] == "you@domain.com"
    assert (v4["ix-click"]["tag"], v4["ix-click"]["role"]) == ("a", "link")
    assert (v4["claim-next"]["tag"], v4["claim-next"]["role"], v4["claim-next"]["text"]) == ("div", "button", "Next step")
    assert v4["limit-thumb"]["accessible_name"] == "Limit of cover"
    assert v4["claim-contact-email"]["accessible_name"] == "Email address"
    assert v4["pay-name"]["accessible_name"] == "Cardholder name"
    assert ["claim-field-location", "claim-field-incident-time"] in locator_map("v4")["structure"]["swaps"]


def test_locator_map_default_and_unknown_release(client: TestClient) -> None:
    assert client.get("/api/locator-map").json()["release"] == "v1"
    client.post("/sandbox/release", json={"release": "v4"}, headers=SANDBOX)
    assert client.get("/api/locator-map").json()["release"] == "v4"
    res = client.get("/api/locator-map", params={"release": "v9"})
    assert res.status_code == 400 and "unknown release" in res.json()["detail"]


def test_v4_is_a_valid_release(client: TestClient) -> None:
    assert client.post("/sandbox/release", json={"release": "v4"}, headers=SANDBOX).json()["release"] == "v4"
    assert client.get("/api/config").json()["release"] == "v4"


def test_sandbox_drift(client: TestClient) -> None:
    assert client.post("/sandbox/drift", json={"seconds": 10}).status_code == 401
    assert client.post("/sandbox/drift", json={"seconds": 10}, headers=SANDBOX).json() == {"status": "ok", "drift_seconds": 10}
    assert client.get("/api/config").json()["drift_seconds"] == 10
    assert client.get("/sandbox/status", headers=SANDBOX).json()["drift_seconds"] == 10
    for bad in (1, 301, -3):
        assert client.post("/sandbox/drift", json={"seconds": bad}, headers=SANDBOX).status_code == 400
    assert client.post("/sandbox/drift", json={"seconds": 0}, headers=SANDBOX).json()["drift_seconds"] == 0
    kinds = [(a["kind"], a["summary"]) for a in client.get("/api/activity").json()]
    assert kinds == [("drift", "Live drift every 10 s"), ("drift", "Live drift switched off")]
    client.post("/sandbox/drift", json={"seconds": 5}, headers=SANDBOX)
    client.post("/sandbox/reset", headers=SANDBOX)
    assert client.get("/api/config").json()["drift_seconds"] == 0


def test_drift_setting(monkeypatch, tmp_path) -> None:
    assert [drift_seconds(v) for v in ("0", "", "2", "300", 15)] == [0, 0, 2, 300, 15]
    for bad in ("1", "301", "-2"):
        with pytest.raises(ValueError, match="drift must be"):
            drift_seconds(bad)
    monkeypatch.setenv("LAB_DRIFT_SECONDS", "20")
    assert Settings().drift_seconds == 20
    c = TestClient(create_app(make_settings(tmp_path, drift_seconds=20)))
    assert c.get("/healthz").json()["drift_seconds"] == 20


def test_release_assets_served(client: TestClient) -> None:
    assert client.get("/static/releases.json").json()["releases"] == ["v1", "v2", "v3", "v4"]
