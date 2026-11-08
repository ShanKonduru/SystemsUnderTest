"""Every MCP tool of the Harbor Mutual tool server, per persona (customer, guest, representative), plus its stdio
entry points. Calls go straight to the server; failures must come back as ToolErrors carrying the backend's code."""

import json
import runpy
import sqlite3
import sys
import warnings

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from insurance_copilot.backend.db import InsuranceBackend
from insurance_copilot.mcp_server import build_server, http as http_mod, server as server_mod
from tests.conftest import TODAY


def _server(db, principal="CUST-001", txn_policy="direct"):
    return build_server(InsuranceBackend(db, principal, TODAY, txn_policy))


async def ok(server, name, args=None):
    result = await server.call_tool(name, args or {})
    assert not getattr(result, "is_error", False)
    return json.loads(result.content[0].text)


async def refused(server, name, args=None):
    with pytest.raises(ToolError) as exc:
        await server.call_tool(name, args or {})
    return str(exc.value).removeprefix(f"Error executing tool {name}: ")


def audit(db):
    return sqlite3.connect(db).execute("SELECT tool, outcome FROM audit_log ORDER BY rowid").fetchall()


async def test_customer_read_tools(db):
    s = _server(db)
    assert (await ok(s, "get_customer_profile"))["id"] == "CUST-001"
    assert {p["code"] for p in (await ok(s, "list_products"))["products"]} == {
        "AUTO",
        "HOME",
        "RENTERS",
        "TRAVEL",
        "LIFE",
    }
    assert (await ok(s, "list_drivers", {"policy_id": "POL-1001"}))["primary_driver"]["full_name"] == "Shan"
    riders = await ok(s, "list_riders", {"policy_id": "POL-1002"})
    assert riders["policy_id"] == "POL-1002" and riders["product_code"] == "HOME"
    assert (await ok(s, "list_documents")) == {"documents": []}
    reqs = await ok(s, "get_change_requirements", {"change_type": "mailing_address"})
    assert reqs["change_type"] == "mailing_address"
    doc = await ok(s, "get_policy_document", {"policy_id": "POL-1002", "document_type": "declarations"})
    assert doc["document_type"] == "declarations" and doc["document_id"].startswith("DOC-")
    assert all(outcome == "ok" for _, outcome in audit(db))


async def test_customer_quote_and_write_tools(db):
    s = _server(db)
    quote = await ok(
        s,
        "get_quote",
        {
            "product_code": "RENTERS",
            "coverage_level": "basic",
            "deductible": 500,
            "insured_item": {"property_address": "12 Pier Rd, Springfield, IL 62704", "personal_property_value": 20000},
        },
    )
    assert quote["quote_id"].startswith("QTE-") and quote["product_code"] == "RENTERS"
    # buying needs the disclosure the app adds only after the customer approved the prompt
    assert (await refused(s, "purchase_policy", {"quote_id": quote["quote_id"]})).startswith("CONSENT_REQUIRED")
    changed = await ok(s, "update_policy_coverage", {"policy_id": "POL-1002", "deductible": 1000})
    assert changed["policy_id"] == "POL-1002" and changed["new"]["deductible"] == 1000
    added = await ok(
        s,
        "add_driver",
        {"policy_id": "POL-1001", "full_name": "Rowan Quay", "date_of_birth": "1990-06-15", "relationship": "spouse"},
    )
    assert added["policy_id"] == "POL-1001"
    assert (await refused(s, "remove_driver", {"policy_id": "POL-1001", "driver_id": "DRV-0000"})).startswith(
        "NOT_FOUND"
    )
    assert (await refused(s, "remove_rider", {"policy_id": "POL-1002", "rider_code": "FLOOD"})).startswith("NOT_FOUND")
    assert ("purchase_policy", "error") in audit(db) and ("update_policy_coverage", "ok") in audit(db)


async def test_malformed_dates_are_invalid_requests(db):
    s = _server(db)
    msg = await refused(
        s,
        "file_claim",
        {
            "policy_id": "POL-1001",
            "incident_date": "2026-13-45",
            "claim_type": "collision",
            "description": "dent",
            "estimated_amount": 900,
        },
    )
    assert msg.startswith("INVALID_REQUEST:")
    assert audit(db)[-1] == ("file_claim", "error")


async def test_guest_tools(db):
    s = _server(db, "GUEST-HARBOR01")
    names = {t.name for t in await s.list_tools()}
    assert {"start_application", "get_application_status", "request_representative", "get_quote"} <= names
    assert "get_customer_profile" not in names
    assert (await refused(s, "get_application_status", {"application_id": "APP-0000"})).startswith("NOT_FOUND")
    app = {
        "quote_id": "QTE-0000",
        "full_name": "Marina Coast",
        "email": "marina@example.com",
        "date_of_birth": "1990-01-01",
        "address": "3 Harbor Way, Springfield, IL 62704",
    }
    assert (await refused(s, "start_application", app)).startswith("CONSENT_REQUIRED")
    assert (
        await refused(
            s,
            "request_representative",
            {"topic": "quote question", "summary": "Asked about renters", "preferred_channel": "chat"},
        )
    ).startswith("CONSENT_REQUIRED")


async def test_representative_tools(db):
    s = _server(db, "REP-203")
    assert (await refused(s, "end_customer_session")).startswith("INVALID_REQUEST")
    assert (await refused(s, "get_service_history")).startswith("CUSTOMER_NOT_VERIFIED")
    assert (await refused(s, "add_note", {"text": "called in"})).startswith("CUSTOMER_NOT_VERIFIED")
    assert (await refused(s, "complete_task", {"task_id": "NTE-0000"})).startswith("NOT_FOUND")
    queue = await ok(s, "list_work_queue")
    assert "requests_pending_review" in queue
    assert (await refused(s, "review_request", {"transaction_id": "TXN-0000", "decision": "approve"})).startswith(
        "NOT_FOUND"
    )
    assert (await refused(s, "issue_application", {"application_id": "APP-0000"})).startswith("CONSENT_REQUIRED")
    assert (await refused(s, "decline_application", {"application_id": "APP-0000", "reason": "incomplete"})).startswith(
        "NOT_FOUND"
    )
    assert (await refused(s, "update_handoff", {"handoff_id": "HND-0000", "action": "assign"})).startswith("NOT_FOUND")

    await ok(s, "verify_customer", {"customer_id": "CUST-001", "date_of_birth": "1985-04-12", "postal_code": "62701"})
    history = await ok(s, "get_service_history")
    assert isinstance(history, dict)
    note = await ok(s, "add_note", {"text": "Customer asked about billing dates", "follow_up_on": "2026-11-10"})
    assert json.dumps(note)
    assert (await ok(s, "end_customer_session")) is not None
    assert (await refused(s, "get_service_history")).startswith("CUSTOMER_NOT_VERIFIED")


def test_stdio_entry_point(monkeypatch, db):
    started = {}

    def fake_run(self, transport):
        started.update(name=self.name, transport=transport, instructions=self.instructions)

    monkeypatch.setattr(MCPServer, "run", fake_run)
    monkeypatch.setenv("COPILOT_CUSTOMER_ID", "CUST-002")
    monkeypatch.setenv("COPILOT_DB", str(db))
    monkeypatch.setenv("COPILOT_TODAY", "2026-11-03")
    server_mod.main()
    assert started["transport"] == "stdio" and "CUST-002" in started["instructions"]
    monkeypatch.delenv("COPILOT_TODAY")
    server_mod.main()
    assert started["transport"] == "stdio"


@pytest.mark.parametrize(
    "argv, expected",
    [
        ([], ("stdio",)),
        (["--http", "9123"], ("http", 9123, "127.0.0.1")),
        (["--http", "9124", "--host", "0.0.0.0"], ("http", 9124, "0.0.0.0")),
    ],
)
def test_package_runs_as_a_module(monkeypatch, argv, expected):
    seen = []
    monkeypatch.setattr(server_mod, "main", lambda: seen.append(("stdio",)))
    monkeypatch.setattr(http_mod, "serve", lambda port, host: seen.append(("http", port, host)))
    monkeypatch.setattr(sys, "argv", ["insurance_copilot.mcp_server", *argv])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("insurance_copilot.mcp_server", run_name="__main__")
    assert seen == [expected]
