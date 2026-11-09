"""The planted defects (buggy build for test runs) and the OpenTelemetry GenAI spans."""

from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest

from claims_agent import defects
from claims_agent.agent import ClaimsAgent, inprocess_connector
from claims_agent.backend import ClaimsBackend
from claims_agent.config import Settings
from claims_agent.policy import RulesLLM, factory
from claims_agent.telemetry import EpisodeSpans, export

INTAKE = {"type": "claim.document_received", "claim_id": "CLM-5003", "document_id": "DOC-78"}
SETTLE = {"type": "claim.assessment_completed", "claim_id": "CLM-5004", "decision": "approve", "amount": 1500}


def notes(settings) -> int:
    con = sqlite3.connect(settings.db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM claim_notes").fetchone()[0]
    finally:
        con.close()


async def run(settings, event, principal="adjuster-07", llm=None):
    return await ClaimsAgent(llm or RulesLLM(settings.defects), settings, inprocess_connector(settings)).handle(
        event, principal
    )


def test_defect_names_are_checked(monkeypatch):
    assert defects.parse(None) == frozenset() and defects.parse(" loop, vague_letter ,") == {"loop", "vague_letter"}
    with pytest.raises(ValueError, match="unknown CLAIMS_DEFECTS"):
        defects.parse("loop,typo")
    monkeypatch.setenv("CLAIMS_DEFECTS", "silent_write")
    monkeypatch.setenv("CLAIMS_OTEL_FILE", "spans.jsonl")
    s = Settings()
    assert s.defects == {"silent_write"} and s.otel_file == "spans.jsonl"
    assert factory({"CLAIMS_DEFECTS": "loop"}).defects == {"loop"} and factory({}).defects == frozenset()


def test_backend_defects(settings):
    clean = ClaimsBackend(settings.db_path, settings.letters_dir, "adjuster-07", settings.today)
    clean.get_claim("CLM-5003")
    assert notes(settings) == 0
    silent = ClaimsBackend(settings.db_path, settings.letters_dir, "adjuster-07", settings.today, {"silent_write"})
    silent.get_claim("CLM-5003")
    assert notes(settings) == 1  # a read that wrote
    phantom = ClaimsBackend(settings.db_path, settings.letters_dir, "adjuster-07", settings.today, {"phantom_note"})
    assert phantom.add_note("CLM-5003", "hello") == {"claim_id": "CLM-5003", "note_id": 2}
    assert notes(settings) == 1  # reported success, saved nothing


async def test_policy_defects(settings):
    settings.max_steps = 3
    loop = await run(settings, INTAKE, llm=RulesLLM(frozenset({"loop"})))
    assert loop.outcome == "max_steps" and [s["name"] for s in loop.steps] == ["get_claim"] * 3
    settings.max_steps = 10
    vague = await run(settings, INTAKE, llm=RulesLLM(frozenset({"vague_letter"})))
    assert vague.done
    letter = (settings.letters_dir / "CLM-5003-acknowledgement.txt").read_text(encoding="utf-8")
    assert "DOC-78" not in letter and "your document" in letter
    pays = await run(settings, SETTLE, llm=RulesLLM(frozenset({"pay_on_settlement"})))
    assert [s["name"] for s in pays.steps][-1] == "make_payment" and not pays.steps[-1]["ok"]  # adjuster may not pay


async def test_trace_omits_reads_but_the_spans_do_not(settings, tmp_path):
    settings.defects = frozenset({"trace_omits_read"})
    settings.otel_file = str(tmp_path / "otel" / "spans.jsonl")
    ep = await run(settings, INTAKE)
    assert [s["name"] for s in ep.steps] == ["update_claim", "write_letter"] and ep.done
    (line,) = (tmp_path / "otel" / "spans.jsonl").read_text(encoding="utf-8").splitlines()
    spans = json.loads(line)["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [s["name"] for s in spans] == [
        "invoke_agent claims_agent",
        "execute_tool get_claim",
        "execute_tool update_claim",
        "execute_tool write_letter",
    ]
    attrs = {a["key"]: next(iter(a["value"].values())) for a in spans[1]["attributes"]}
    assert attrs["gen_ai.operation.name"] == "execute_tool" and attrs["enduser.id"] == "adjuster-07"
    assert json.loads(attrs["gen_ai.tool.call.arguments"]) == {"claim_id": "CLM-5003"}
    assert all(s["parentSpanId"] == spans[0]["spanId"] for s in spans[1:])


def test_spans_mark_failed_tools_and_failed_episodes():
    spans = EpisodeSpans("intake-bot")
    spans.tool("update_claim", "t1", {"claim_id": "CLM-5003"}, False, "ACCESS_DENIED: intake-bot lacks", 1)
    payload = spans.request(None, "error")
    root, tool = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert root["status"]["code"] == 2 and tool["status"] == {"code": 2, "message": "ACCESS_DENIED: intake-bot lacks"}
    assert {"key": "error.type", "value": {"stringValue": "tool_error"}} in tool["attributes"]
    keys = {a["key"]: a["value"] for a in root["attributes"]}
    assert keys["claims.route"] == {"stringValue": ""}


class _Receiver(BaseHTTPRequestHandler):
    got: ClassVar[list[tuple[str | None, dict[str, Any]]]] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Receiver.got.append((self.headers.get("Authorization"), json.loads(body)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def test_export_posts_to_an_otlp_endpoint_and_never_raises(tmp_path, caplog):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/traces"
        export({"resourceSpans": []}, "", url, "tok")
        export({"resourceSpans": [1]}, "", url, "")
        assert _Receiver.got == [("Bearer tok", {"resourceSpans": []}), (None, {"resourceSpans": [1]})]
    finally:
        server.shutdown()
        thread.join(timeout=5)
    export({}, str(tmp_path), "http://127.0.0.1:1/v1/traces", "")  # a folder as the file; nothing listening
    assert "could not write spans" in caplog.text and "could not export spans" in caplog.text


def test_settings_load_without_python_dotenv(monkeypatch):
    import runpy
    import sys

    from claims_agent import config

    monkeypatch.setitem(sys.modules, "dotenv", None)  # importing it now raises ImportError
    namespace = runpy.run_path(config.__file__, run_name="config_without_dotenv")
    assert namespace["PROJECT_ROOT"] == config.PROJECT_ROOT
