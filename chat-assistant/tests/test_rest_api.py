"""The insurance backend as a REST API, and the app's REST toolbox, give the same results as MCP."""

import asyncio
import json

try:
    import httpx2 as httpx
except ImportError:
    import httpx

from insurance_copilot.backend.db import InsuranceBackend
from insurance_copilot.core.toolbox import RestToolbox, specs_from_listing
from insurance_copilot.mcp_server import build_server
from insurance_copilot.rest_api import ROUTES, InsuranceApi, openapi, route_of
from tests.conftest import TODAY


def _toolbox(db, principal):
    app = InsuranceApi(db, TODAY).app()
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api",
                             headers={"X-Customer-Id": principal})
    return http, RestToolbox(http, "http://api/v1")


def test_routes_are_unique_and_cover_every_tool(db):
    async def names(principal):
        return {t.name for t in await build_server(InsuranceBackend(db, principal, TODAY)).list_tools()}
    every = asyncio.run(names("CUST-001")) | asyncio.run(names("REP-203"))
    assert every <= set(ROUTES), every - set(ROUTES)
    assert len({(m, p) for m, p, _ in ROUTES.values()}) == len(ROUTES)
    assert len({op for _, _, op in ROUTES.values()}) == len(ROUTES)
    assert route_of("brand_new_tool") == ("POST", "/operations/brand_new_tool", "brand_new_tool")


def test_rest_toolbox_matches_mcp_for_reads_writes_and_errors(db):
    async def run():
        mcp = build_server(InsuranceBackend(db, "CUST-001", TODAY))
        http, tb = _toolbox(db, "CUST-001")
        async with http:
            await tb.load()
            mcp_specs = specs_from_listing([t.model_dump(by_alias=True, exclude_none=True, mode="json")
                                            for t in await mcp.list_tools()])
            assert tb.tools == mcp_specs                                   # same tools, schemas and metadata
            assert tb.routes["make_payment"] == ("POST", "/policies/{policy_id}/payments")
            billing = await tb.call("get_billing", {"policy_id": "POL-1002"})
            assert not billing.is_error and json.loads(billing.text)["balance_due"] == 193.6
            listed = await tb.call("list_policies", {"include_cancelled": False})
            assert {p["id"] for p in json.loads(listed.text)["policies"]} == {"POL-1001", "POL-1002"}
            paid = await tb.call("make_payment", {"policy_id": "POL-1002", "amount": 50, "payment_method": "card_on_file"})
            assert not paid.is_error and "PAY-" in paid.text
            denied = await tb.call("get_policy_details", {"policy_id": "POL-2001"})
            assert denied.is_error and denied.access_denied
            refused = await tb.call("add_rider", {"policy_id": "POL-1002", "rider_code": "RENTAL_CAR"})
            assert refused.is_error and "ACCESS_DENIED" not in refused.text
            missing = await tb.call("get_billing", {})
            assert missing.is_error and "policy_id is required" in missing.text
            rep_only = await http.get("/v1/reps/me")
            assert rep_only.status_code == 404                              # a customer has no representative tools
    asyncio.run(run())


def test_rest_api_status_codes_and_openapi_document(db):
    async def run():
        http, _ = _toolbox(db, "CUST-001")
        async with http:
            assert (await http.get("/v1/policies/POL-2001")).status_code == 403
            assert (await http.get("/v1/policies/POL-1002")).status_code == 200
            assert (await http.post("/v1/policies/POL-1002/payments", content=b"not json")).status_code == 400
            assert (await http.get("/v1/policies", headers={"X-Customer-Id": ""})).status_code == 403
            spec = (await http.get("/v1/openapi.json")).json()
        assert spec["paths"]["/policies/{policy_id}/payments"]["post"]["operationId"] == "createPayment"
        assert spec["paths"]["/reps/me"]["get"]["operationId"] == "getMyRole"
        assert spec["paths"]["/policies/{policy_id}/riders/{rider_code}"]["delete"]["operationId"] == "removeRider"
        pay = spec["paths"]["/policies/{policy_id}/payments"]["post"]
        assert pay["parameters"][0]["name"] == "policy_id" and "amount" in pay["requestBody"]["content"]["application/json"]["schema"]["properties"]
    asyncio.run(run())


def test_representative_operations_over_rest(db):
    async def run():
        http, tb = _toolbox(db, "REP-203")
        async with http:
            await tb.load()
            assert len(tb.tools) == 38
            role = await tb.call("get_my_role", {})
            assert not role.is_error and json.loads(role.text)["role"] == "supervisor"
            verified = await tb.call("verify_customer", {"customer_id": "CUST-001", "date_of_birth": "1985-04-12", "postal_code": "62701"})
            assert not verified.is_error, verified.text
            billing = await tb.call("get_billing", {"policy_id": "POL-1002"})
            assert not billing.is_error
    asyncio.run(run())


def test_openapi_lists_every_route_once():
    class T:
        def __init__(self, name):
            self.name, self.input_schema, self.description = name, {"type": "object", "properties": {"policy_id": {"type": "string"}}}, name
    spec = openapi([T(n) for n in ROUTES])
    ops = [op["operationId"] for item in spec["paths"].values() for op in item.values()]
    assert sorted(ops) == sorted(op for _, _, op in ROUTES.values())
