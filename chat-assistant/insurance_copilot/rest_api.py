"""The insurance backend as a REST API, for an assistant that calls APIs directly instead of an MCP tool server.

    python -m insurance_copilot.rest_api --port 9200        # http://127.0.0.1:9200/v1/...

Every operation is one of the MCP tools, served at a resource-style route (see ROUTES) with an OpenAPI operationId,
and runs the same tool function with the same rules, so an assistant gets identical behaviour over MCP or REST.

- The signed-in principal (customer, guest or representative) travels in the X-Customer-Id header, as in the MCP
  HTTP mode; each principal gets its own backend and sees only its own operations (404 for the others).
- Path parameters are the tool's required ids; GET and DELETE take the other arguments as query parameters, other
  methods as a JSON body. Errors keep the backend's code: ACCESS_DENIED -> 403, NOT_FOUND -> 404, others -> 400,
  with {"error": "<CODE>: <message>"}.
- GET /v1/tools lists the principal's operations with their schema, metadata and route (the app discovers them
  here); GET /v1/openapi.json is the API's OpenAPI document.

Sandbox/demo only, like the MCP HTTP mode: the API trusts the X-Customer-Id header.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from insurance_copilot.backend.db import BackendError, InsuranceBackend
from insurance_copilot.mcp_server.server import build_server

PREFIX = "/v1"
CUSTOMER_HEADER = "x-customer-id"
# tool -> (method, path under /v1, operationId). Path parameters are required arguments of the tool.
ROUTES: dict[str, tuple[str, str, str]] = {
    "get_customer_profile": ("GET", "/me", "getMyProfile"),
    "list_policies": ("GET", "/policies", "listPolicies"),
    "get_policy_details": ("GET", "/policies/{policy_id}", "getPolicy"),
    "list_products": ("GET", "/products", "listProducts"),
    "list_claims": ("GET", "/claims", "listClaims"),
    "get_billing": ("GET", "/policies/{policy_id}/billing", "getBilling"),
    "get_quote": ("POST", "/quotes", "createQuote"),
    "purchase_policy": ("POST", "/quotes/{quote_id}/purchase", "purchaseQuote"),
    "update_policy_coverage": ("PATCH", "/policies/{policy_id}/coverage", "updateCoverage"),
    "list_drivers": ("GET", "/policies/{policy_id}/drivers", "listDrivers"),
    "add_driver": ("POST", "/policies/{policy_id}/drivers", "addDriver"),
    "remove_driver": ("DELETE", "/policies/{policy_id}/drivers/{driver_id}", "removeDriver"),
    "list_riders": ("GET", "/riders", "listRiders"),
    "add_rider": ("PUT", "/policies/{policy_id}/riders/{rider_code}", "addRider"),
    "remove_rider": ("DELETE", "/policies/{policy_id}/riders/{rider_code}", "removeRider"),
    "get_change_requirements": ("GET", "/profile-changes/requirements/{change_type}", "getChangeRequirements"),
    "list_documents": ("GET", "/documents", "listDocuments"),
    "submit_profile_change": ("POST", "/profile-changes", "submitProfileChange"),
    "file_claim": ("POST", "/policies/{policy_id}/claims", "fileClaim"),
    "make_payment": ("POST", "/policies/{policy_id}/payments", "createPayment"),
    "cancel_policy": ("POST", "/policies/{policy_id}/cancellation", "cancelPolicy"),
    "get_transaction_status": ("GET", "/transactions/{transaction_id}", "getTransaction"),
    "list_transactions": ("GET", "/transactions", "listTransactions"),
    "get_policy_document": ("POST", "/policies/{policy_id}/documents", "issuePolicyDocument"),
    "request_representative": ("POST", "/handoffs", "requestRepresentative"),
    "start_application": ("POST", "/applications", "startApplication"),
    "get_application_status": ("GET", "/applications/{application_id}", "getApplication"),
    "get_my_role": ("GET", "/reps/me", "getMyRole"),
    "search_customers": ("GET", "/customers", "searchCustomers"),
    "verify_customer": ("POST", "/customers/{customer_id}/verification", "verifyCustomer"),
    "end_customer_session": ("DELETE", "/service-context", "endCustomerSession"),
    "get_service_history": ("GET", "/service-context/history", "getServiceHistory"),
    "add_note": ("POST", "/service-context/notes", "addNote"),
    "complete_task": ("POST", "/tasks/{task_id}/completion", "completeTask"),
    "list_work_queue": ("GET", "/work-queue", "listWorkQueue"),
    "review_request": ("POST", "/transactions/{transaction_id}/review", "reviewRequest"),
    "issue_application": ("POST", "/applications/{application_id}/issue", "issueApplication"),
    "decline_application": ("POST", "/applications/{application_id}/decline", "declineApplication"),
    "update_handoff": ("PATCH", "/handoffs/{handoff_id}", "updateHandoff"),
}
STATUS = {"ACCESS_DENIED": 403, "NOT_FOUND": 404}


def route_of(tool: str) -> tuple[str, str, str]:
    """(method, path, operationId); a tool added later without a route is served RPC-style."""
    return ROUTES.get(tool, ("POST", f"/operations/{tool}", tool))


def path_params(path: str) -> list[str]:
    return re.findall(r"{([^}/]+)}", path)


def _coerce(value: str, schema: dict[str, Any]) -> Any:
    """Query and path values are text; turn them into what the tool's JSON schema expects."""
    types = {schema.get("type")} | {s.get("type") for s in schema.get("anyOf", []) if isinstance(s, dict)}
    if value == "" and "null" in types:
        return None
    if "boolean" in types and value.lower() in ("true", "false"):
        return value.lower() == "true"
    if "integer" in types and re.fullmatch(r"-?\d+", value):
        return int(value)
    if "number" in types and re.fullmatch(r"-?\d+(\.\d+)?", value):
        return float(value)
    return value


def tool_listing(tool: Any) -> dict[str, Any]:
    d = tool.model_dump(by_alias=True, exclude_none=True, mode="json")
    method, path, op = route_of(d["name"])
    d["rest"] = {"method": method, "path": path, "operationId": op}    # path relative to the API base URL
    return d


def openapi(tools: list[Any]) -> dict[str, Any]:
    """OpenAPI 3.0 document for the given tools (the union over principals is the whole API)."""
    paths: dict[str, dict[str, Any]] = {}
    for t in sorted(tools, key=lambda t: t.name):
        method, path, op = route_of(t.name)
        schema = t.input_schema or {}
        props = schema.get("properties") or {}
        params = [{"name": p, "in": "path", "required": True, "schema": props.get(p, {"type": "string"})} for p in path_params(path)]
        rest = {k: v for k, v in props.items() if k not in path_params(path)}
        entry: dict[str, Any] = {"operationId": op, "summary": (t.description or "").strip().split("\n")[0][:200],
                                 "responses": {"200": {"description": "OK"}, "400": {"description": "Business rule refused"},
                                               "403": {"description": "ACCESS_DENIED"}, "404": {"description": "NOT_FOUND"}}}
        if method in ("GET", "DELETE"):
            params += [{"name": k, "in": "query", "required": k in (schema.get("required") or []), "schema": v}
                       for k, v in rest.items()]
        elif rest:
            entry["requestBody"] = {"content": {"application/json": {"schema": {
                "type": "object", "properties": rest, "required": [r for r in schema.get("required") or [] if r in rest]}}}}
        if params:
            entry["parameters"] = params
        paths.setdefault(path, {})[method.lower()] = entry
    return {"openapi": "3.0.3", "info": {"title": "Harbor Mutual insurance API (sandbox)", "version": "1.0"},
            "servers": [{"url": PREFIX}], "paths": paths}


class InsuranceApi:
    def __init__(self, db_path: Path | str, today: date | None, txn_policy: str = "direct"):
        self.db_path, self.today, self.txn_policy = db_path, today, txn_policy
        self.servers: dict[str, Any] = {}

    def server_for(self, principal: str) -> Any:
        if principal not in self.servers:
            self.servers[principal] = build_server(InsuranceBackend(self.db_path, principal, self.today, self.txn_policy))
        return self.servers[principal]

    async def _principal(self, request: Request) -> Any:
        principal = request.headers.get(CUSTOMER_HEADER, "")
        if not principal:
            raise PermissionError("missing X-Customer-Id header")
        return self.server_for(principal)

    async def tools(self, request: Request) -> Response:
        try:
            server = await self._principal(request)
        except (PermissionError, BackendError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        return JSONResponse({"tools": [tool_listing(t) for t in await server.list_tools()]})

    async def spec(self, request: Request) -> Response:
        names: dict[str, Any] = {}
        for principal in ("CUST-001", "REP-203"):          # a customer and a supervisor between them see every tool
            try:
                for t in await self.server_for(principal).list_tools():
                    names.setdefault(t.name, t)
            except BackendError:
                continue
        return JSONResponse(openapi(list(names.values())))

    def endpoint(self, tool: str, method: str, path: str):
        async def handle(request: Request) -> Response:
            try:
                server = await self._principal(request)
            except (PermissionError, BackendError) as exc:
                return JSONResponse({"error": str(exc)}, status_code=403)
            listed = {t.name: t for t in await server.list_tools()}
            if tool not in listed:
                return JSONResponse({"error": f"NOT_FOUND: no operation {tool} for this principal"}, status_code=404)
            props = (listed[tool].input_schema or {}).get("properties") or {}
            args: dict[str, Any] = {k: _coerce(v, props.get(k, {})) for k, v in request.path_params.items()}
            if method in ("GET", "DELETE"):
                args.update({k: _coerce(v, props.get(k, {})) for k, v in request.query_params.items()})
            else:
                raw = await request.body()
                if raw:
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        return JSONResponse({"error": "INVALID_REQUEST: the body is not JSON"}, status_code=400)
                    if not isinstance(body, dict):
                        return JSONResponse({"error": "INVALID_REQUEST: the body must be a JSON object"}, status_code=400)
                    args.update(body)
            try:
                result = await server.call_tool(tool, args)
            except Exception as exc:  # noqa: BLE001 - ToolError and validation errors: the message is the contract
                text = re.sub(rf"^Error executing tool {re.escape(tool)}: ", "", str(exc))
                code = text.split(":", 1)[0].strip()
                return JSONResponse({"error": text}, status_code=STATUS.get(code, 400))
            text = "\n".join(c.text for c in result.content if getattr(c, "type", None) == "text")
            if getattr(result, "is_error", False):
                return JSONResponse({"error": text}, status_code=400)
            try:
                return JSONResponse(json.loads(text), status_code=201 if method == "POST" else 200)
            except ValueError:
                return Response(text, media_type="text/plain", status_code=201 if method == "POST" else 200)
        return handle

    def app(self) -> Starlette:
        routes = [Route(PREFIX + "/tools", self.tools, methods=["GET"]),
                  Route(PREFIX + "/openapi.json", self.spec, methods=["GET"])]
        seen: dict[tuple[str, str], str] = {}
        for tool, (method, path, _) in ROUTES.items():
            assert (method, path) not in seen, f"{tool} and {seen.get((method, path))} share {method} {path}"
            seen[(method, path)] = tool
            routes.append(Route(PREFIX + path, self.endpoint(tool, method, path), methods=[method]))
        routes.append(Route(PREFIX + "/operations/{tool}", self._rpc, methods=["POST"]))
        return Starlette(routes=routes)

    async def _rpc(self, request: Request) -> Response:
        tool = request.path_params["tool"]
        if tool in ROUTES:
            return JSONResponse({"error": f"NOT_FOUND: {tool} is served at {route_of(tool)[1]}"}, status_code=404)
        return await self.endpoint(tool, "POST", f"/operations/{tool}")(request)


def serve(port: int, host: str = "127.0.0.1") -> None:
    import uvicorn

    db = os.environ.get("COPILOT_DB") or str(Path(__file__).resolve().parents[1] / "data" / "insurance.db")
    today = date.fromisoformat(os.environ["COPILOT_TODAY"]) if os.environ.get("COPILOT_TODAY") else None
    print(f"Insurance REST API on http://{host}:{port}{PREFIX} · {db}")
    uvicorn.run(InsuranceApi(db, today, os.environ.get("COPILOT_TXN_POLICY", "direct")).app(), host=host, port=port,
                log_level="warning")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="The insurance backend as a REST API (sandbox/demo).")
    p.add_argument("--port", type=int, default=9200)
    p.add_argument("--host", default="127.0.0.1")
    a = p.parse_args()
    serve(a.port, a.host)
