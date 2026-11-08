"""Terminal chat: rendering of each stage, /attach, approvals, traces and the command-line entry point."""

import argparse
import asyncio
import contextlib
import json
import runpy
import sqlite3
import sys
import warnings

import pytest

from insurance_copilot import cli
from insurance_copilot.core import session as session_mod
from insurance_copilot.core.trace import Event
from tests.conftest import intent, plan
from tests.fakes import ScriptedLLM, text, tools

PDF = b"%PDF-1.4\n% proof\n%%EOF\n"


@pytest.fixture
def fake_session(monkeypatch):
    """Real engine and tools in-process, scripted LLM."""
    llm = ScriptedLLM()
    real_open = session_mod.open_session

    @contextlib.asynccontextmanager
    async def fake_open(**kwargs):
        async with real_open(**kwargs, llm=llm) as s:
            yield s

    monkeypatch.setattr(cli, "open_session", fake_open)
    monkeypatch.setenv("COPILOT_TODAY", "2026-11-03")
    return llm, fake_open


def feed(monkeypatch, *answers):
    """Script what the user types; running out of input behaves like Ctrl+D."""
    queue, prompts = list(answers), []

    def fake_input(prompt=""):
        prompts.append(prompt)
        if not queue:
            raise EOFError
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


def args(tmp_path, **overrides):
    base = dict(
        customer="CUST-001",
        db=str(tmp_path / "cli.db"),
        reset_db=False,
        auto_approve=False,
        transport="inprocess",
        trace=None,
        verbose=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_render_prints_every_stage(capsys):
    cli.render(
        Event(
            "intent",
            {
                "intents": ["billing", "file_claim"],
                "confidence": "high",
                "entities": [{"name": "policy_id", "value": "POL-1002"}],
            },
        ),
        False,
    )
    cli.render(Event("intent", {"intents": ["general_question"], "confidence": "low", "entities": []}), False)
    cli.render(Event("tools_selected", {"tools": []}), False)
    cli.render(Event("tools_selected", {"tools": ["get_billing", "make_payment"]}), False)
    cli.render(Event("plan", {"steps": [{"action": "get_billing"}, {"action": "respond"}]}), False)
    cli.render(Event("tool_call", {"name": "get_billing", "arguments": {"policy_id": "POL-1002"}}), False)
    base = {
        "name": "get_billing",
        "result": "line one\nline two",
        "is_error": False,
        "blocked_reason": None,
        "access_denied": False,
    }
    cli.render(Event("tool_result", base), True)
    cli.render(Event("tool_result", {**base, "is_error": True}), False)
    cli.render(Event("tool_result", {**base, "is_error": True, "access_denied": True}), False)
    cli.render(Event("tool_result", {**base, "is_error": True, "blocked_reason": "USER_DECLINED"}), False)
    cli.render(Event("error", {"message": "planner: truncated"}), False)
    cli.render(Event("turn_started", {"turn": 1}), False)  # not shown
    out = capsys.readouterr().out
    assert "[1 intent] billing, file_claim (high)  policy_id=POL-1002" in out
    assert "[1 intent] general_question (low)\x1b" in out
    assert "[tools]    none" in out and "[tools]    get_billing, make_payment" in out
    assert "[2 plan]   get_billing -> respond" in out
    assert '[3 call]   get_billing({"policy_id": "POL-1002"})' in out
    assert "get_billing: ok" in out and "line one line two" in out
    assert "get_billing: error" in out and "get_billing: ACCESS_DENIED" in out and "get_billing: USER_DECLINED" in out
    assert "[error] planner: truncated" in out
    assert out.count("\n") == 11


def test_chat_attach_approve_and_save_traces(tmp_path, monkeypatch, capsys, fake_session):
    llm, _ = fake_session
    llm.structured_queue += [
        intent("billing"),
        plan("make_payment", "respond"),
        intent("billing"),
        plan("make_payment", "respond"),
    ]
    llm.executor_queue += [
        tools(("make_payment", {"policy_id": "POL-1002", "amount": 20})),
        text("Paid $20."),
        tools(("make_payment", {"policy_id": "POL-1002", "amount": 30})),
        text("Not paid."),
    ]
    proof = tmp_path / "electric bill.pdf"
    proof.write_bytes(PDF)
    (tmp_path / "notes.txt").write_text("not a proof")
    trace_file = tmp_path / "traces.jsonl"
    feed(
        monkeypatch,
        "",
        f'/attach "{proof}"',
        f"/attach {tmp_path / 'missing.pdf'}",
        f"/attach {tmp_path / 'notes.txt'}",
        "pay $20 on my home policy",
        "y",
        "pay $30 too",
        "no",
        "exit",
    )
    asyncio.run(cli.chat(args(tmp_path, trace=str(trace_file), verbose=True)))
    out = capsys.readouterr().out
    assert "Seeded demo database" in out
    assert "attached DOC-6001 electric bill.pdf" in out
    assert out.count("could not attach") == 2 and "Only PDF, JPG, PNG or WebP" in out
    assert "assistant>\x1b[0m Paid $20." in out and "assistant>\x1b[0m Not paid." in out
    # the attachment went out with the next message only
    first, second = llm.create_calls[0]["messages"][0]["content"], llm.create_calls[2]["messages"][-2]["content"]
    assert "[Attached document DOC-6001: electric bill.pdf" in first and "Attached" not in second
    lines = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines()]
    assert [t["turn"] for t in lines] == [1, 2]
    assert lines[0]["tool_calls"][0]["confirmed"] is True and lines[1]["tool_calls"][0]["confirmed"] is False
    paid = sqlite3.connect(tmp_path / "cli.db").execute("SELECT amount FROM payments WHERE amount IN (20, 30)")
    assert [r[0] for r in paid] == [20]


def test_chat_auto_approve_and_ctrl_c(tmp_path, monkeypatch, capsys, fake_session):
    llm, _ = fake_session
    llm.structured_queue += [intent("cancel_policy"), plan("cancel_policy", "respond")]
    llm.executor_queue += [
        tools(("cancel_policy", {"policy_id": "POL-1002", "reason": "sold the house"})),
        text("Cancelled."),
    ]
    db = tmp_path / "cli.db"
    prompts = feed(monkeypatch, "quit")
    asyncio.run(cli.chat(args(tmp_path)))  # first run seeds the database
    assert "Seeded demo database" in capsys.readouterr().out
    prompts = feed(monkeypatch, "cancel my home policy", KeyboardInterrupt())
    asyncio.run(cli.chat(args(tmp_path, auto_approve=True)))
    out = capsys.readouterr().out
    assert "Seeded demo database" not in out  # existing database is kept
    assert "-> auto-approved" in out and "Cancelled." in out
    assert not any("Approve?" in p for p in prompts)
    status = sqlite3.connect(db).execute("SELECT status FROM policies WHERE id='POL-1002'").fetchone()[0]
    assert status == "cancelled"


def test_destructive_approval_prompt_warns(tmp_path, monkeypatch, fake_session):
    llm, _ = fake_session
    llm.structured_queue += [intent("cancel_policy"), plan("cancel_policy", "respond")]
    llm.executor_queue += [tools(("cancel_policy", {"policy_id": "POL-1002", "reason": "sold"})), text("Kept.")]
    prompts = feed(monkeypatch, "cancel my home policy", "N")
    asyncio.run(cli.chat(args(tmp_path, reset_db=True)))
    assert any("(cannot be undone)" in p and "Approve? [y/N]" in p for p in prompts)


def test_main_parses_arguments(monkeypatch, tmp_path):
    seen = {}

    async def fake_chat(a):
        seen.update(vars(a))

    monkeypatch.setattr(cli, "chat", fake_chat)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "--customer",
            "CUST-002",
            "--db",
            str(tmp_path / "x.db"),
            "--reset-db",
            "--auto-approve",
            "--transport",
            "inprocess",
            "--trace",
            "t.jsonl",
            "-v",
        ],
    )
    cli.main()
    assert seen == {
        "customer": "CUST-002",
        "db": str(tmp_path / "x.db"),
        "reset_db": True,
        "auto_approve": True,
        "transport": "inprocess",
        "trace": "t.jsonl",
        "verbose": True,
    }


def test_main_exits_quietly_on_ctrl_c(monkeypatch):
    async def interrupted(a):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "chat", interrupted)
    monkeypatch.setattr(sys, "argv", ["cli"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def test_module_runs_as_a_script(monkeypatch, tmp_path, capsys, fake_session):
    _, fake_open = fake_session
    monkeypatch.setattr(session_mod, "open_session", fake_open)  # the script imports it afresh
    monkeypatch.setenv("COPILOT_DB", str(tmp_path / "m.db"))  # database and transport from the environment
    monkeypatch.setenv("COPILOT_MCP_TRANSPORT", "inprocess")
    monkeypatch.setattr(sys, "argv", ["cli", "--customer", "CUST-003"])
    feed(monkeypatch, "exit")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # the module is already imported by this test file
        runpy.run_module("insurance_copilot.cli", run_name="__main__")
    assert "signed in as CUST-003" in capsys.readouterr().out


def test_settings_load_without_python_dotenv(monkeypatch):
    """python-dotenv is optional at import time: without it the real environment is used."""
    from insurance_copilot import config

    monkeypatch.setitem(sys.modules, "dotenv", None)  # importing it now raises ImportError
    namespace = runpy.run_path(config.__file__, run_name="config_without_dotenv")
    assert namespace["PROJECT_ROOT"] == config.PROJECT_ROOT
