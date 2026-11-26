from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from billing import letters


def test_long_date_and_dollars() -> None:
    assert letters.long_date("2027-01-31") == "January 31, 2027"
    assert letters.long_date(date(2026, 12, 7)) == "December 7, 2026"
    assert letters.dollars("1411.6") == "$1,411.60"
    assert letters.dollars(Decimal("98")) == "$98.00"


def test_renewal_notice_letter(client: TestClient) -> None:
    offer = client.get("/policies/P-5001/renewal").json()
    response = client.get("/policies/P-5001/renewal-notice")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert 'class="letter renewal-notice"' in html
    assert 'data-policy="P-5001"' in html
    assert f'data-sent="{offer["notice_date"]}"' in html
    assert "Dear Asha Rao," in html
    assert "<p>December 17, 2026</p>" in html
    assert "<strong>January 31, 2027</strong>" in html
    assert "<td>$1,411.60</td>" in html
    assert "at least 45 days" in html


def test_no_letter_without_a_renewal_offer(client: TestClient) -> None:
    response = client.get("/policies/P-5004/renewal-notice")
    assert response.status_code == 409
    assert response.json() == {"error": "conflict", "detail": "no renewal notice for policy P-5004: policy is lapsed"}
    assert client.get("/policies/P-9999/renewal-notice").status_code == 404


def test_letter_text_is_escaped() -> None:
    html = letters.render(
        "renewal_notice.html",
        policy={"id": "P-1", "product": "auto", "coverage": "basic", "plan": "annual"},
        customer={"name": "<b>Robert & Sons</b>"},
        renewal={"notice_date": "2026-12-17", "renewal_date": "2027-01-31", "renewal_premium": "100.00"},
        notice_days=45,
    )
    assert "Dear &lt;b&gt;Robert &amp; Sons&lt;/b&gt;," in html
