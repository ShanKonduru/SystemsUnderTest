from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from automation_lab.app import create_app
from automation_lab.config import Settings
from automation_lab.defects import KNOWN, parse
from automation_lab.domain import price_quote
from tests.conftest import TODAY, claim, make_settings, quote


def lab(tmp_path, *defects: str) -> TestClient:
    return TestClient(create_app(make_settings(tmp_path, defects=frozenset(defects))))


def test_parse_defects() -> None:
    assert parse(None) == frozenset() and parse(" slow_ui , phantom_claim,") == {"slow_ui", "phantom_claim"}
    with pytest.raises(ValueError, match="unknown LAB_DEFECTS"):
        parse("slow_ui,typo")
    assert set(KNOWN) == {"discount_ignored", "phantom_claim", "upload_drops_last", "board_not_saved", "slow_ui"}


def test_discount_ignored(tmp_path) -> None:
    body = lab(tmp_path, "discount_ignored").post("/api/quotes", json=quote()).json()
    assert body["annual_premium"] == 647.96
    assert not any("Multi-policy" in line["label"] for line in body["breakdown"])


def test_phantom_claim(tmp_path) -> None:
    c = lab(tmp_path, "phantom_claim")
    assert c.post("/api/claims", json=claim()).json()["claim_id"] == "CLM-9001"
    assert c.get("/api/claims").json() == []


def test_upload_drops_last(tmp_path) -> None:
    c = lab(tmp_path, "upload_drops_last")
    res = c.post("/api/uploads", files=[("files", ("a.txt", b"a", "text/plain")), ("files", ("b.txt", b"b", "text/plain"))]).json()
    assert [u["name"] for u in res["uploaded"]] == ["a.txt", "b.txt"]
    assert [u["name"] for u in c.get("/api/uploads").json()] == ["a.txt"]
    single = c.post("/api/uploads", files=[("files", ("c.txt", b"c", "text/plain"))]).json()
    assert c.get("/api/uploads/" + single["uploaded"][0]["id"]).content == b"c"


def test_board_not_saved(tmp_path) -> None:
    c = lab(tmp_path, "board_not_saved")
    assert c.put("/api/board/cards/CLM-5002", json={"column": "paid", "position": 0}).json()["column"] == "paid"
    assert next(x for x in c.get("/api/board").json() if x["id"] == "CLM-5002")["column"] == "new"
    assert c.put("/api/board/cards/CLM-0000", json={"column": "paid", "position": 0}).status_code == 404


def test_slow_ui_is_sent_to_the_page(tmp_path) -> None:
    assert lab(tmp_path, "slow_ui", "phantom_claim").get("/api/config").json()["ui_defects"] == ["slow_ui"]


def test_settings_from_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAB_DATA", str(tmp_path))
    monkeypatch.setenv("LAB_TODAY", "2027-01-15")
    monkeypatch.setenv("LAB_RELEASE", "V2")
    monkeypatch.setenv("LAB_MAX_UPLOAD_MB", "0.5")
    monkeypatch.setenv("LAB_DEFECTS", "slow_ui")
    s = Settings()
    assert (s.data_dir, s.today, s.release, s.max_upload_bytes, s.defects) == (tmp_path, date(2027, 1, 15), "v2", 524288, {"slow_ui"})
    assert s.db_path == tmp_path / "lab.db" and s.uploads_dir == tmp_path / "uploads"
    monkeypatch.setenv("LAB_RELEASE", "v7")
    with pytest.raises(ValueError, match="unknown LAB_RELEASE"):
        Settings()


def test_pricing_age_and_coverage() -> None:
    young = price_quote(quote(date_of_birth="2004-01-01", addons=[], multi_policy=False, paperless=False, coverage="premium", deductible=500), TODAY)
    assert young["annual_premium"] == round(620 * 1.3 * 1.10 * 1.35, 2)
    senior = price_quote(quote(product="boat", date_of_birth="1950-01-01", addons=["glass"], multi_policy=False, paperless=False, deductible=2500, coverage="basic"), TODAY)
    assert senior["annual_premium"] == round(410 * 0.85 * 0.88 * 1.15 + 36, 2)


def test_page_ids_used_by_the_script_exist() -> None:
    """Every literal $("id") in app.js names an element authored in index.html (a typo would break a release)."""
    import re
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "automation_lab" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    script = (static / "app.js").read_text(encoding="utf-8")
    authored = set(re.findall(r'\sid="([^"]+)"', html))
    used = set(re.findall(r'\$\("([a-z0-9-]+)"\)', script))
    assert used and used - authored == set()
