"""The host's checks that keep the assistant's behaviour the same however a customer phrases a request.

Found by TruAssure's tone run (the same turn asked panicked, aggressive, jargon-heavy...):
- under pressure the model went straight to add_driver for a 14-year-old and the customer was asked to approve a
  change the backend refuses; the host now refuses it first, without an approval prompt (the same for a deductible or
  coverage level that isn't offered, and a proof document of a kind a change never accepts: SH-06, IN-09);
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


async def test_a_deductible_that_is_not_offered_is_refused_before_any_approval_prompt(settings):
    llm = ScriptedLLM(
        structured=[intent("modify_policy"), plan("update_policy_coverage", "respond")],
        executor=[tools(("update_policy_coverage", {"policy_id": "POL-1001", "deductible": 750})),
                  text("$750 isn't an option: choose $250, $500, $1,000 or $2,000.")],
    )
    (trace,), asked = await _run(settings, llm, ["Change the deductible on my car policy to $750."])
    call = trace.tool_calls[0]
    assert asked == [] and call.blocked_reason == "INVALID_REQUEST" and not call.executed
    assert "$750 deductible isn't offered" in call.result and "$250, $500, $1,000 and $2,000" in call.result


async def test_a_coverage_level_that_is_not_offered_is_refused(settings):
    llm = ScriptedLLM(
        structured=[intent("modify_policy"), plan("update_policy_coverage", "respond")],
        executor=[tools(("update_policy_coverage", {"policy_id": "POL-1001", "coverage_level": "gold"})),
                  text("There is no gold level.")],
    )
    (trace,), asked = await _run(settings, llm, ["Make my car policy gold."])
    assert asked == [] and trace.tool_calls[0].blocked_reason == "INVALID_REQUEST"
    assert "basic, standard, premium" in trace.tool_calls[0].result


async def test_a_proof_of_the_wrong_kind_is_refused_before_any_approval_prompt(settings):
    bill = {"change_type": "date_of_birth", "new_value": "1990-10-03", "document_id": "DOC-6002",
            "document_type": "utility_bill", "reason": "typo at sign-up"}
    llm = ScriptedLLM(
        structured=[intent("update_profile"), plan("submit_profile_change", "respond")],
        executor=[tools(("submit_profile_change", bill)), text("A utility bill can't prove a date of birth.")],
    )
    (trace,), asked = await _run(settings, llm, ["My date of birth is wrong; electricity bill attached."])
    call = trace.tool_calls[0]
    assert asked == [] and call.blocked_reason == "INVALID_REQUEST" and not call.executed
    assert "utility bill is not accepted" in call.result and "passport" in call.result


async def test_valid_coverage_and_proof_still_go_to_the_customer(settings):
    cases = [
        ("modify_policy", "update_policy_coverage", {"policy_id": "POL-1001", "deductible": "$1,000"}),
        ("update_profile", "submit_profile_change",
         {"change_type": "date_of_birth", "new_value": "1990-10-03", "document_id": "DOC-6002",
          "document_type": "passport", "reason": "typo"}),
    ]
    for intent_name, tool, args in cases:
        llm = ScriptedLLM(structured=[intent(intent_name), plan(tool, "respond")],
                          executor=[tools((tool, args)), text("Declined.")])
        (trace,), asked = await _run(settings, llm, ["do it"], approve=False)
        assert len(asked) == 1 and trace.tool_calls[0].blocked_reason == "USER_DECLINED", tool


QUESTIONS = [
    "If I cancel my car policy today, how much refund would I get back?",
    "What refund would I get if I cancelled my car policy today?",
    "If I were to cancel my auto policy today, how much would I be refunded?",
    "Just wondering - if my car policy was cancelled today, what would come back to me as a refund?",
    "What would the pro-rata return premium be if I cancelled my auto policy effective today?",
    "If I stop my car insurance today, how much of my money do I get back?",
    "Refund if car policy cancelled today?",
]
REQUESTS = ["Cancel my home insurance policy, please.", "If you can, cancel my car policy today.",
            "Cancel POL-1001 if that's possible.", "Please cancel it, I'm moving abroad."]


def test_a_question_about_cancelling_is_told_apart_from_a_request():
    from insurance_copilot.core.engine import CANCEL_QUESTION
    assert all(CANCEL_QUESTION.search(q) for q in QUESTIONS)
    assert not any(CANCEL_QUESTION.search(r) for r in REQUESTS)


async def test_a_question_about_cancelling_never_reaches_an_approval_prompt(settings):
    llm = ScriptedLLM(
        structured=[intent("cancel_policy", "billing"), plan("get_balance", "respond")],
        executor=[tools(("cancel_policy", {"policy_id": "POL-1001"})),
                  text("I can't work out a cancellation refund here. Shall I cancel it?")],
    )
    (trace,), asked = await _run(settings, llm, [QUESTIONS[0]])
    call = trace.tool_calls[0]
    assert asked == [] and call.blocked_reason == "OUT_OF_SCOPE" and not call.executed
    assert "cancel_policy" not in trace.selected_tools
    note = next(m["content"] for m in llm.create_calls[0]["messages"] if m["role"] == "system")
    assert "not a request to cancel" in note and "No tool calculates a cancellation refund" in note


async def test_a_request_to_cancel_still_goes_to_the_customer(settings):
    llm = ScriptedLLM(
        structured=[intent("cancel_policy"), plan("cancel_policy", "respond")],
        executor=[tools(("cancel_policy", {"policy_id": "POL-1001"})), text("Not cancelled.")],
    )
    (trace,), asked = await _run(settings, llm, ["If you can, cancel my car policy today."], approve=False)
    assert len(asked) == 1 and trace.tool_calls[0].blocked_reason == "USER_DECLINED"


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


async def test_the_hosts_lookup_is_handed_to_the_model_and_never_repeated(settings):
    """SH-06: the host looked up the policy and the model looked it up again (a duplicate call)."""
    llm = ScriptedLLM(
        structured=[intent("modify_policy", policy_id="POL-1001"), plan("update_policy_coverage", "respond")],
        executor=[text("Your deductible is $500 today; I can change it to $750.")],
    )
    (trace,), _ = await _run(settings, llm, ["Change the deductible on my car policy to $750."], customer="CUST-001")
    (lookup,) = trace.tool_calls
    assert lookup.step == 0 and lookup.name == "get_policy_details"
    note = llm.create_calls[0]["messages"][-1]["content"]
    assert "Already looked up by the app this turn" in note and '{"policy_id": "POL-1001"}' in note


async def test_the_models_own_lookups_mean_no_host_lookup_later(settings):
    """A policy the model already saw (list_policies or get_policy_details) is known: no second lookup."""
    llm = ScriptedLLM(
        structured=[intent("view_policies"), plan("list_policies", "respond"),
                    intent("cancel_policy", policy_id="POL-1002"), plan("cancel_policy", "respond")],
        executor=[tools(("list_policies", {})), text("You have POL-1001 and POL-1002."),
                  tools(("cancel_policy", {"policy_id": "POL-1002"})), text("Not cancelled; POL-1002 is still active.")],
    )
    (_, second), _ = await _run(settings, llm, ["What policies do I have?", "Cancel POL-1002."],
                                customer="CUST-001", approve=False)
    assert [c.name for c in second.tool_calls] == ["cancel_policy"]


async def test_a_denied_lookup_by_the_model_is_remembered(settings):
    llm = ScriptedLLM(
        structured=[intent("view_policies"), plan("get_policy_details", "respond"), intent("cancel_policy")],
        executor=[tools(("get_policy_details", {"policy_id": "POL-2001"})), text("That isn't on your account."),
                  text("POL-2001 isn't on your account, so I can't cancel it.")],
    )
    (_, second), asked = await _run(settings, llm, ["Show POL-2001.", "Cancel POL-2001."], customer="CUST-001")
    assert second.tool_calls == [] and second.plan is None and asked == []
