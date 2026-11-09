import sqlite3

from mcp import Client

from insurance_copilot.backend.db import InsuranceBackend
from insurance_copilot.core.toolbox import McpToolbox
from insurance_copilot.mcp_server import build_server
from tests.conftest import TODAY


async def _toolbox(db, customer="CUST-001"):
    client = Client(build_server(InsuranceBackend(db, customer, TODAY)))
    return client


async def test_tools_carry_intent_and_confirmation_metadata(db):
    async with await _toolbox(db) as client:
        tb = McpToolbox(client)
        await tb.load()
    writes = {n for n, s in tb.tools.items() if s.requires_confirmation}
    assert writes == {"purchase_policy", "update_policy_coverage", "submit_profile_change", "file_claim", "add_driver", "remove_driver", "add_rider", "remove_rider",
                      "make_payment", "cancel_policy", "request_representative"}
    for name in writes:
        assert not tb.tools[name].read_only
    assert tb.tools["cancel_policy"].destructive
    assert all(spec.intents for spec in tb.tools.values())
    assert set(tb.tools_for_intents(["billing"])) == {"list_policies", "get_policy_details", "get_billing",
                                                      "make_payment"}


async def test_access_denied_is_returned_and_audited(db):
    async with await _toolbox(db) as client:
        tb = McpToolbox(client)
        await tb.load()
        out = await tb.call("get_policy_details", {"policy_id": "POL-2001"})
    assert out.is_error and out.access_denied
    assert out.text.startswith("ACCESS_DENIED")
    rows = sqlite3.connect(db).execute("SELECT tool, outcome FROM audit_log").fetchall()
    assert rows == [("get_policy_details", "denied")]


async def test_successful_call_returns_json(db):
    async with await _toolbox(db) as client:
        tb = McpToolbox(client)
        await tb.load()
        out = await tb.call("list_policies", {})
    assert not out.is_error and "POL-1001" in out.text and "POL-2001" not in out.text
