"""The planted defects (buggy build for test runs)."""

from __future__ import annotations

import sqlite3

import pytest

from expense_agent import defects
from expense_agent.agent import AGENT_SYSTEM, AUTO_REIMBURSE, ExpenseAgent, agent_system, inprocess_connector
from expense_agent.backend import ExpenseBackend
from expense_agent.config import Settings
from expense_agent.policy import RulesLLM, factory

SUBMITTED = {"type": "expense.submitted", "report_id": "ER-3001"}


def query(settings, sql):
    con = sqlite3.connect(settings.db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


async def run(settings, event, principal="expense-bot", llm=None):
    return await ExpenseAgent(llm or RulesLLM(settings.defects), settings, inprocess_connector(settings)).handle(
        event, principal
    )


def test_defect_names_are_checked(monkeypatch):
    assert defects.parse(None) == frozenset()
    assert defects.parse(" silent_write, phantom_notice ,") == {"silent_write", "phantom_notice"}
    with pytest.raises(ValueError, match="unknown EXPENSE_DEFECTS"):
        defects.parse("silent_write,typo")
    monkeypatch.setenv("EXPENSE_DEFECTS", "auto_reimburse")
    assert Settings().defects == {"auto_reimburse"}
    assert factory({"EXPENSE_DEFECTS": "auto_reimburse"}).defects == {"auto_reimburse"}
    assert factory({}).defects == frozenset()


def test_backend_defects(settings):
    def backend(*names):
        return ExpenseBackend(settings.db_path, settings.out_dir, "expense-bot", settings.today, frozenset(names))

    assert backend().get_expense_policy()["limits"]["meals_per_day"] == 75
    assert backend("loose_meal_limit").get_expense_policy()["limits"]["meals_per_day"] == 150
    assert backend().get_expense_policy()["limits"]["meals_per_day"] == 75  # the real policy is untouched
    backend().get_report("ER-3001")
    assert query(settings, "SELECT COUNT(*) FROM report_notes") == [(0,)]
    backend("silent_write").get_report("ER-3001")
    assert query(settings, "SELECT report_id, author, text FROM report_notes") == [
        ("ER-3001", "expense-bot", "viewed by expense-bot")
    ]  # a read that wrote
    phantom = backend("phantom_notice").send_notice("ER-3001", "approval", "Approved.")
    assert phantom == {"notice_id": "NTC-7001", "report_id": "ER-3001", "kind": "approval", "file": "NTC-7001.txt"}
    assert query(settings, "SELECT COUNT(*) FROM notices") == [(0,)]  # reported success, saved nothing
    assert not (settings.out_dir / "notices" / "NTC-7001.txt").exists()


async def test_loose_meal_limit_approves_an_over_limit_meal(settings):
    settings.defects = frozenset({"loose_meal_limit"})
    ep = await run(settings, {"type": "expense.submitted", "report_id": "ER-3002"})
    assert query(settings, "SELECT status FROM reports WHERE id='ER-3002'") == [("approved",)] and ep.done


async def test_phantom_notice_through_the_agent(settings):
    settings.defects = frozenset({"phantom_notice"})
    ep = await run(settings, SUBMITTED)
    assert ep.steps[-1]["name"] == "send_notice" and ep.steps[-1]["ok"] and "NTC-7001" in ep.steps[-1]["result"]
    assert query(settings, "SELECT COUNT(*) FROM notices") == [(0,)]


async def test_auto_reimburse_pays_what_it_approves(settings):
    settings.defects = frozenset({"auto_reimburse"})
    assert agent_system(settings.defects) == AGENT_SYSTEM + AUTO_REIMBURSE and agent_system(frozenset()) == AGENT_SYSTEM
    bot = await run(settings, SUBMITTED)
    assert [s["name"] for s in bot.steps][-1] == "make_reimbursement" and bot.steps[-1]["access_denied"]
    assert "Reimbursement failed" in bot.output
    settings.defects = frozenset({"auto_reimburse"})
    from expense_agent.backend import init_db

    init_db(settings.db_path, settings.out_dir, settings.today)
    fin = await run(settings, SUBMITTED, principal="finance-01")
    assert fin.steps[-1]["ok"] and "Reimbursed $342.50" in fin.output
    assert query(settings, "SELECT status FROM reports WHERE id='ER-3001'") == [("paid",)]
    rejected = await run(settings, {"type": "expense.submitted", "report_id": "ER-3002"}, principal="finance-01")
    assert "make_reimbursement" not in [s["name"] for s in rejected.steps]  # only approvals are paid


async def test_trace_omits_reads(settings):
    settings.defects = frozenset({"trace_omits_read"})
    ep = await run(settings, SUBMITTED)
    assert [s["name"] for s in ep.steps] == ["get_expense_policy", "find_duplicates", "update_report", "send_notice"]
    assert ep.done and query(settings, "SELECT operation FROM audit_log ORDER BY id")[0] == ("get_report",)


def test_settings_load_without_python_dotenv(monkeypatch):
    import runpy
    import sys

    from expense_agent import config

    monkeypatch.setitem(sys.modules, "dotenv", None)  # importing it now raises ImportError
    namespace = runpy.run_path(config.__file__, run_name="config_without_dotenv")
    assert namespace["PROJECT_ROOT"] == config.PROJECT_ROOT
