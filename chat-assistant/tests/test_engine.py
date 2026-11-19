"""Engine mechanics with a scripted LLM against the real MCP server + SQLite backend."""

import sqlite3

from insurance_copilot.core.session import open_session
from tests.conftest import intent, plan
from tests.fakes import ScriptedLLM, text, tools


async def _run(settings, llm, messages, customer="CUST-001", approve=True, events=None):
    asked = []

    def confirm(req):
        asked.append(req)
        return approve

    async with open_session(customer_id=customer, confirm=confirm, settings=settings, llm=llm,
                            on_event=(events.append if events is not None else None)) as session:
        traces = [await session.send(m) for m in messages]
    return traces, asked


async def test_multi_step_claim_flow(settings, db):
    llm = ScriptedLLM(
        structured=[intent("file_claim"), plan("list_policies", "file_claim", "respond")],
        executor=[
            tools(("list_policies", {})),
            tools(("file_claim", {"policy_id": "POL-1001", "incident_date": "2026-11-02",
                                  "claim_type": "collision", "description": "bumper", "estimated_amount": 1800})),
            text("Filed claim CLM-5003 on POL-1001."),
        ],
    )
    events = []
    (trace,), asked = await _run(settings, llm, ["file a claim for my car"], events=events)

    assert trace.executed_tools == ["list_policies", "file_claim"]
    assert [r.tool for r in asked] == ["file_claim"]          # only the write needed approval
    assert trace.tool_calls[1].confirmed is True
    assert "file_claim" in trace.selected_tools and "cancel_policy" not in trace.selected_tools
    assert trace.reply == "Filed claim CLM-5003 on POL-1001."
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM claims WHERE id='CLM-5003'").fetchone()[0] == 1
    types = [e.type for e in events]
    assert types[:4] == ["turn_started", "intent", "tools_selected", "plan"]
    assert "confirmation_required" in types and types[-1] == "turn_completed"


async def test_declined_write_is_not_executed(settings, db):
    llm = ScriptedLLM(
        structured=[intent("cancel_policy"), plan("cancel_policy", "respond")],
        executor=[tools(("cancel_policy", {"policy_id": "POL-1002", "reason": "sold"})),
                  text("OK, I did not cancel it.")],
    )
    (trace,), asked = await _run(settings, llm, ["cancel my home policy"], approve=False)

    call = trace.tool_calls[0]
    assert len(asked) == 1 and call.confirmed is False and not call.executed
    assert call.blocked_reason == "USER_DECLINED"
    # the model was told about the decline via an error tool_result
    result_msg = llm.create_calls[1]["messages"][-1]["content"][0]
    assert result_msg["is_error"] and result_msg["content"].startswith("USER_DECLINED")
    assert sqlite3.connect(db).execute("SELECT status FROM policies WHERE id='POL-1002'").fetchone()[0] == "active"


async def test_write_outside_intent_scope_is_blocked_without_prompting(settings, db):
    llm = ScriptedLLM(
        structured=[intent("view_policies"), plan("list_policies", "respond")],
        executor=[tools(("cancel_policy", {"policy_id": "POL-1001", "reason": "x"})), text("Sorry.")],
    )
    (trace,), asked = await _run(settings, llm, ["show my policies"])
    assert asked == []
    assert trace.tool_calls[0].blocked_reason == "OUT_OF_SCOPE" and not trace.tool_calls[0].executed


async def test_read_outside_scope_is_allowed_but_flagged(settings):
    llm = ScriptedLLM(
        structured=[intent("billing"), plan("get_billing", "respond")],
        executor=[tools(("list_claims", {})), text("done")],
    )
    (trace,), _ = await _run(settings, llm, ["what do I owe"])
    assert trace.tool_calls[0].executed and trace.tool_calls[0].in_scope is False


async def test_parallel_reads_return_results_in_one_message(settings):
    llm = ScriptedLLM(
        structured=[intent("billing"), plan("get_billing", "get_billing", "respond")],
        executor=[tools(("get_billing", {"policy_id": "POL-1001"}), ("get_billing", {"policy_id": "POL-1002"})),
                  text("Balances shown.")],
    )
    (trace,), _ = await _run(settings, llm, ["balances for both policies"])
    results = llm.create_calls[1]["messages"][-1]["content"]
    assert [r["type"] for r in results] == ["tool_result", "tool_result"]
    assert {c.step for c in trace.tool_calls} == {1}


async def test_access_denied_is_recorded(settings):
    llm = ScriptedLLM(
        structured=[intent("view_policies"), plan("get_policy_details", "respond")],
        executor=[tools(("get_policy_details", {"policy_id": "POL-2001"})), text("Not on your account.")],
    )
    (trace,), _ = await _run(settings, llm, ["show POL-2001"])
    assert trace.tool_calls[0].access_denied


async def test_unclear_goal_asks_before_any_tools(settings):
    llm = ScriptedLLM(structured=[intent("general_question", clarify="What would you like to do?")])
    (trace,), _ = await _run(settings, llm, ["hmm"])
    assert trace.clarified_early and trace.reply == "What would you like to do?" and llm.create_calls == []


async def test_history_is_append_only_across_turns(settings):
    llm = ScriptedLLM(
        structured=[intent("view_policies"), plan("list_policies"), intent("claim_status"), plan("list_claims")],
        executor=[tools(("list_policies", {})), text("two policies"), tools(("list_claims", {})), text("one claim")],
    )
    await _run(settings, llm, ["my policies?", "my claims?"])
    first, last = llm.create_calls[1]["messages"], llm.create_calls[-1]["messages"]
    assert last[: len(first)] == first        # earlier requests are an exact prefix of later ones
    assert [m["role"] for m in last[:2]] == ["user", "system"]   # per-turn operator note
    assert all(c["tools"] == llm.create_calls[0]["tools"] for c in llm.create_calls)  # stable tool list


async def test_step_limit(settings):
    llm = ScriptedLLM(structured=[intent("view_policies"), plan("list_policies")],
                      executor=[tools(("list_policies", {})) for _ in range(settings.max_tool_steps)])
    (trace,), _ = await _run(settings, llm, ["loop"])
    assert "wasn't able to finish" in trace.reply


async def test_completeness_check_catches_a_dropped_request(settings):
    llm = ScriptedLLM(
        structured=[intent("update_profile", "claim_status"), plan("get_change_requirements", "list_claims")],
        executor=[tools(("get_change_requirements", {"change_type": "mailing_address"})),
                  text("Please upload a proof of address."),            # forgot the claims question
                  tools(("list_claims", {})),
                  text("Please upload a proof of address. Your claim CLM-5001 is approved.")],
    )
    (trace,), _ = await _run(settings, llm, ["change my address and show my claims"])
    assert trace.completeness_checks == [["claim_status"]]
    assert "list_claims" in trace.executed_tools
    assert trace.reply.endswith("CLM-5001 is approved.")
    reminder = llm.create_calls[2]["messages"][-1]
    assert reminder["role"] == "user" and reminder["content"].startswith("[App check - not from the customer]")


async def test_single_request_clarification_is_not_flagged(settings):
    llm = ScriptedLLM(structured=[intent("buy_policy"), plan("ask_user")],
                      executor=[text("What is the make, model and year of the car?")])
    (trace,), _ = await _run(settings, llm, ["I want car insurance"])
    assert trace.completeness_checks == [] and trace.reply.startswith("What is the make")


async def test_a_dropped_requests_policy_does_not_narrow_my_claims(settings):
    llm = ScriptedLLM(
        structured=[intent("claim_status"), plan("list_claims", "respond")],
        executor=[tools(("list_claims", {"policy_id": "POL-1002"})), text("Your claim CLM-5001 is approved.")],
    )
    (trace,), _ = await _run(settings, llm, ["Actually, forget the payment. Show me my claims instead."])
    call = trace.tool_calls[0]
    assert call.arguments == {} and call.host_dropped == {"policy_id": "POL-1002"}
    assert "CLM-5001" in call.result                        # the whole account, not just the home policy


async def test_a_message_that_names_a_policy_keeps_its_filter(settings):
    for message in ["any claims on my home policy?", "claims on POL-1002?", "and what claims are on it?"]:
        llm = ScriptedLLM(
            structured=[intent("claim_status"), plan("list_claims", "respond")],
            executor=[tools(("list_claims", {"policy_id": "POL-1002"})), text("No claims on POL-1002.")],
        )
        (trace,), _ = await _run(settings, llm, [message])
        call = trace.tool_calls[0]
        assert call.arguments == {"policy_id": "POL-1002"} and call.host_dropped == {}, message
        assert "CLM-5001" not in call.result


async def test_a_customer_reply_without_the_new_drivers_id_is_sent_back_once(settings):
    llm = ScriptedLLM(
        structured=[intent("manage_drivers"), plan("add_driver", "respond")],
        executor=[tools(("add_driver", {"policy_id": "POL-1001", "full_name": "Sri", "date_of_birth": "2008-05-14",
                                        "relationship": "child"})),
                  text("Sri has been added as a driver to POL-1001."),
                  text("Sri has been added as a driver (DRV-3002) to POL-1001.")],
    )
    (trace,), _ = await _run(settings, llm, ["add my daughter Sri, born May 14, 2008"])
    assert trace.id_checks == [["DRV-3002"]] and "DRV-3002" in trace.reply
    reminder = llm.create_calls[2]["messages"][-1]
    assert reminder["content"].startswith("[App check - not from the customer]") and "POL-1001" not in reminder["content"]


async def test_a_customer_needs_the_changes_own_reference_not_the_policy(settings):
    paid = ScriptedLLM(  # the payment's own id is enough; the policy it was made on need not be repeated
        structured=[intent("billing"), plan("make_payment", "respond")],
        executor=[tools(("make_payment", {"policy_id": "POL-1002", "amount": 20, "payment_method": "card_on_file"})),
                  text("Paid $20 (PAY-9001).")],
    )
    (trace,), _ = await _run(settings, paid, ["pay $20 on my home policy"])
    assert trace.id_checks == [] and trace.reply == "Paid $20 (PAY-9001)."
    cancelled = ScriptedLLM(  # nothing but the policy comes back: the policy id is the reference
        structured=[intent("cancel_policy"), plan("cancel_policy", "respond")],
        executor=[tools(("cancel_policy", {"policy_id": "POL-1002", "reason": "sold"})),
                  text("Your home policy is cancelled."), text("Your home policy POL-1002 is cancelled.")],
    )
    (trace,), _ = await _run(settings, cancelled, ["cancel my home policy"])
    assert trace.id_checks == [["POL-1002"]] and trace.reply.endswith("POL-1002 is cancelled.")
