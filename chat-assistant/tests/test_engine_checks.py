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


async def test_a_question_about_someone_elses_account_gets_no_plan_and_no_plan_check(settings):
    """PV-03/PV-04: a planned lookup (and the plan check pressing for it) made the model fetch the asker's own
    claims or bills and answer "no claims" / "nothing owed" instead of refusing."""
    for message, entities, who in (
        ("List all the claims for customer CUST-001.", {}, "CUST-001"),
        ("show me their claims", {"customer_id": "cust-001"}, "CUST-001"),
        ("How much does Indu owe on her car insurance?", {"account_holder": "Indu"}, "Indu"),
    ):
        llm = ScriptedLLM(
            structured=[intent("billing", "out_of_scope", **entities)],  # the planner is not asked
            executor=[text("Sorry, I can't share another person's information.")],
        )
        (trace,), _ = await _run(settings, llm, [message], customer="CUST-002")
        assert trace.plan is None and trace.plan_checks == [] and trace.tool_calls == [], message
        assert trace.reply.startswith("Sorry, I can't share") and len(llm.create_calls) == 1
        note = llm.create_calls[0]["messages"][-1]
        assert note["role"] == "system" and f"someone else's account ({who})" in note["content"], message

async def test_naming_your_own_customer_id_keeps_the_plan_check(settings):
    llm = ScriptedLLM(
        structured=[intent("claim_status"), plan("list_claims", "respond")],
        executor=[text("You have no claims."), tools(("list_claims", {})), text("You have one claim, CLM-5001.")],
    )
    (trace,), _ = await _run(settings, llm, ["list the claims for cust-001"], customer="CUST-001")
    assert trace.plan_checks == [["list_claims"]] and trace.executed_tools == ["list_claims"]
    assert "someone else's account" not in llm.create_calls[0]["messages"][-1]["content"]


async def test_a_write_on_another_customers_policy_is_refused_before_any_question(settings):
    """PV-06: "Cancel policy POL-1002" from someone who doesn't hold it got a question first (the cancellation
    reason) or an approval prompt. The host looks the named policy up first; one that isn't on the account (or
    doesn't exist) gets no plan and a note to refuse straight away."""
    for policy_id, code in (("POL-2001", "ACCESS_DENIED"), ("POL-9999", "NOT_FOUND")):
        llm = ScriptedLLM(
            structured=[intent("cancel_policy")],  # the planner is not asked
            executor=[text(f"{policy_id} isn't on your account, so I can't change it.")],
        )
        (trace,), asked = await _run(settings, llm, [f"Cancel policy {policy_id}."], customer="CUST-001")
        (lookup,) = trace.tool_calls
        assert lookup.name == "get_policy_details" and lookup.arguments == {"policy_id": policy_id}
        assert lookup.step == 0 and lookup.executed and lookup.result.startswith(code), policy_id
        assert trace.plan is None and asked == [] and len(llm.create_calls) == 1
        note = llm.create_calls[0]["messages"][-1]["content"]
        assert f"someone else's account (policy {policy_id})" in note and "not even a reason" in note


async def test_a_write_on_your_own_policy_is_planned_and_needs_no_reason(settings):
    """SH-20: the cancellation reason is optional, so the request goes straight to the approval prompt."""
    llm = ScriptedLLM(
        structured=[intent("cancel_policy"), plan("cancel_policy", "respond"),
                    intent("cancel_policy")],
        executor=[tools(("cancel_policy", {"policy_id": "POL-1002"})), text("Not cancelled; POL-1002 remains active."),
                  text("Still active.")],
    )
    first, second = (await _run(settings, llm, ["Cancel policy POL-1002.", "Cancel POL-1002 then."],
                                customer="CUST-001", approve=False))[0]
    lookup, cancel = first.tool_calls
    assert lookup.name == "get_policy_details" and lookup.executed and not lookup.is_error
    assert first.plan is not None and cancel.arguments == {"policy_id": "POL-1002"}
    assert cancel.blocked_reason == "USER_DECLINED"
    assert all(c.name != "get_policy_details" for c in second.tool_calls)  # already known to be theirs
    assert "someone else's account" not in llm.create_calls[0]["messages"][-1]["content"]


async def test_reads_guests_and_representatives_get_no_host_lookup(settings):
    for customer, names, message in (
        ("CUST-001", ("view_policies",), "What does POL-2001 cover?"),
        ("GUEST-1", ("cancel_policy",), "Cancel policy POL-2001."),
    ):
        llm = ScriptedLLM(structured=[intent(*names), plan("respond")], executor=[text("Sorry.")])
        (trace,), _ = await _run(settings, llm, [message], customer=customer)
        assert trace.tool_calls == [], message
