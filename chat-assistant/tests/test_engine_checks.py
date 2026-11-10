"""The host's checks that keep the assistant's behaviour the same however a customer phrases a request.

Found by TruAssure's tone run (the same turn asked panicked, aggressive, jargon-heavy...):
- under pressure the model went straight to add_driver for a 14-year-old and the customer was asked to approve a
  change the backend refuses; the host now refuses it first, without an approval prompt;
- under pressure the model answered "is gap cover available?" from an earlier turn's data although its plan said
  to look it up; the host now has a planned lookup run before the reply (once per turn).
"""

import sqlite3

from tests.conftest import intent, plan
from tests.fakes import ScriptedLLM, text, tools
from tests.test_engine import _run

ARUN_14 = {"policy_id": "POL-1001", "full_name": "Arun", "date_of_birth": "2012-03-01", "relationship": "child"}


async def test_an_underage_driver_is_refused_before_any_approval_prompt(settings, db):
    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
    llm = ScriptedLLM(
        structured=[intent("manage_drivers"), plan("add_driver", "respond")],
        executor=[tools(("add_driver", ARUN_14)), text("Arun is 14; drivers must be at least 16.")],
    )
    (trace,), asked = await _run(settings, llm, ["His date of birth is 2012-03-01. Get on with it."])
    call = trace.tool_calls[0]
    assert asked == [] and call.blocked_reason == "INVALID_REQUEST" and not call.executed and call.confirmed is None
    assert "at least 16" in call.result and "is 14 today" in call.result
    sent = llm.create_calls[1]["messages"][-1]["content"][0]
    assert sent["is_error"] and sent["content"].startswith("INVALID_REQUEST")
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM drivers").fetchone()[0] == before


async def test_a_valid_or_unreadable_date_still_goes_to_the_customer(settings):
    for dob in ("2008-05-14", "14/05/2008"):  # an adult; a date the backend reports itself
        llm = ScriptedLLM(
            structured=[intent("manage_drivers"), plan("add_driver", "respond")],
            executor=[tools(("add_driver", {**ARUN_14, "full_name": "Sri", "date_of_birth": dob})), text("Declined.")],
        )
        (trace,), asked = await _run(settings, llm, ["add Sri"], approve=False)
        assert len(asked) == 1 and trace.tool_calls[0].blocked_reason == "USER_DECLINED", dob


async def test_a_planned_lookup_runs_before_the_reply(settings):
    llm = ScriptedLLM(
        structured=[intent("manage_riders"), plan("list_riders", "respond")],
        executor=[text("Yes, gap cover is available."),  # answered from memory
                  tools(("list_riders", {"policy_id": "POL-1001"})),
                  text("Yes: gap cover is $6.00 a month on your car policy.")],
    )
    (trace,), _ = await _run(settings, llm, ["Is gap cover available for my car? Yes or no."])
    assert trace.plan_checks == [["list_riders"]] and trace.executed_tools == ["list_riders"]
    assert trace.reply.startswith("Yes: gap cover is $6.00")
    reminder = llm.create_calls[1]["messages"][-1]
    assert reminder["role"] == "user" and reminder["content"].startswith("[App check - not from the customer]")
    assert "list_riders" in reminder["content"] and "on purpose" in reminder["content"]  # a refusal may stand


async def test_the_plan_check_asks_once_and_ignores_writes_and_tools_out_of_scope(settings):
    once = ScriptedLLM(
        structured=[intent("manage_riders"), plan("list_riders", "respond")],
        executor=[text("Available."), text("Still available.")],  # ignores the reminder: its answer stands
    )
    (trace,), _ = await _run(settings, once, ["gap cover?"])
    assert trace.plan_checks == [["list_riders"]] and trace.reply == "Still available."
    writes = ScriptedLLM(
        structured=[intent("cancel_policy"), plan("cancel_policy", "list_claims", "respond")],
        executor=[text("Which policy do you want to cancel?")],  # a write planned, a read not in scope
    )
    (trace,), _ = await _run(settings, writes, ["cancel"])
    assert trace.plan_checks == [] and trace.reply.startswith("Which policy")
