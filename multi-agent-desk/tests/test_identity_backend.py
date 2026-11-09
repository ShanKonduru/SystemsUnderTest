"""Identity tokens (sign, verify, delegate: never widen) and the backend's checks (permission, ownership, audit)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from desk import identity as idmod
from desk.backend import AccessDenied, BackendError, DeskBackend, Identity, member_permissions


def test_issue_verify_and_delegate_narrows_never_widens():
    t = idmod.issue("M-1001", ["claims:read", "billing:read"], "k", now=1000, ttl=60)
    c = idmod.verify(t, "k", now=1001)
    assert c == {
        "sub": "M-1001",
        "permissions": ["billing:read", "claims:read"],
        "chain": ["triage"],
        "iat": 1000,
        "exp": 1060,
    }
    d = idmod.delegate(
        idmod.issue("M-1001", ["claims:read", "billing:read"], "k"),
        "k",
        "billing",
        narrow_to=["billing:read", "billing:write"],
    )
    dc = idmod.verify(d, "k")
    assert dc["chain"] == ["triage", "billing"] and dc["permissions"] == ["billing:read"]
    assert idmod.verify(idmod.delegate(d, "k", "claims"), "k")["chain"] == ["triage", "billing", "claims"]


@pytest.mark.parametrize(
    ("token", "key", "msg"),
    [
        ("nodot", "k", "malformed"),
        ("@@@.x", "k", "malformed"),
        ("eyJ9.x", "k", "malformed"),
    ],
)
def test_verify_rejects_malformed(token, key, msg):
    with pytest.raises(idmod.IdentityError, match=msg):
        idmod.verify(token, key)


def test_verify_rejects_tampering_expiry_and_missing_key():
    t = idmod.issue("M-1001", ["billing:read"], "k", now=1000, ttl=60)
    _payload, sig = t.split(".")
    forged = idmod._b64(
        json.dumps({"sub": "M-1001", "permissions": ["billing:write"], "chain": ["triage"], "exp": 9e12}).encode()
    )
    with pytest.raises(idmod.IdentityError, match="signature"):
        idmod.verify(f"{forged}.{sig}", "k")
    with pytest.raises(idmod.IdentityError, match="signature"):
        idmod.verify(t, "other-key", now=1001)
    with pytest.raises(idmod.IdentityError, match="expired"):
        idmod.verify(t, "k", now=2000)
    with pytest.raises(idmod.IdentityError, match="not set"):
        idmod.verify(t, "")
    with pytest.raises(idmod.IdentityError, match="not set"):
        idmod.sign({}, "")


def ident(member="M-1001", perms=("billing:read", "claims:read", "cases:write"), chain=("triage", "billing")):
    return Identity(member, frozenset(perms), tuple(chain))


def test_backend_reads_own_records_only(settings):
    b = DeskBackend(settings.db_path, ident(), settings.today)
    assert b.get_billing("POL-1002")["balance_due"] == 193.6
    assert [p["id"] for p in b.list_payments("POL-1002")["payments"]] == ["PAY-8801"]
    assert b.get_claim("CLM-5003")["paid_amount"] == 4000.0
    assert b.list_claims() == {"claims": [{"id": "CLM-5003", "policy_id": "POL-1002", "status": "paid"}]}
    with pytest.raises(AccessDenied, match="another member"):
        b.get_billing("POL-2001")
    with pytest.raises(AccessDenied, match="another member"):
        b.list_payments("POL-2001")
    with pytest.raises(AccessDenied, match="another member"):
        b.get_claim("CLM-7001")
    with pytest.raises(BackendError, match="not found"):
        b.get_billing("POL-0")
    with pytest.raises(BackendError, match="not found"):
        b.get_claim("CLM-0")


def test_backend_permissions_widening_and_unknown_members(settings):
    with pytest.raises(AccessDenied, match="does not hold"):
        DeskBackend(settings.db_path, ident(perms=("billing:read", "billing:write")), settings.today)
    with pytest.raises(AccessDenied, match="unknown member"):
        DeskBackend(settings.db_path, ident(member="M-9"), settings.today)
    narrow = DeskBackend(settings.db_path, ident(member="M-3001", perms=("billing:read",)), settings.today)
    for call in (
        narrow.list_claims,
        lambda: narrow.get_claim("CLM-5003"),
        lambda: narrow.open_case("s", "d"),
        lambda: narrow.make_payment("POL-3001", 1),
    ):
        with pytest.raises(AccessDenied, match="ACCESS_DENIED"):
            call()
    assert member_permissions(settings.db_path, "M-9") is None


def test_cases_and_audit_record_the_agent_chain(settings):
    b = DeskBackend(settings.db_path, ident(), settings.today)
    assert b.open_case(" Help ", " details ") == {"case_id": "CASE-1", "subject": "Help"}
    with pytest.raises(BackendError, match="subject is required"):
        b.open_case(" ", "x")
    b.audit("get_billing", "ok")
    con = sqlite3.connect(settings.db_path)
    try:
        assert con.execute("SELECT opened_by FROM cases").fetchone() == ("billing",)
        assert con.execute("SELECT member_id, agent, chain, outcome FROM audit_log").fetchone() == (
            "M-1001",
            "billing",
            '["triage", "billing"]',
            "ok",
        )
    finally:
        con.close()
    assert Identity("M-1", frozenset(), ()).agent == "?"
