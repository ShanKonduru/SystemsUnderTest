"""Engine edge cases: failed turns, no tools in scope, planner failure, refusals, truncation and unknown tools."""

from dataclasses import dataclass

import pytest

from insurance_copilot.core import engine as engine_mod
from insurance_copilot.core.llm import StructuredOutputError
from insurance_copilot.core.session import open_session
from insurance_copilot.core.trace import Plan, TurnTrace
from tests.conftest import intent, plan
from tests.fakes import FakeMessage, ScriptedLLM, TextBlock, text, tools


async def _send(settings, llm, message, customer="CUST-001", events=None):
    async with open_session(customer_id=customer, confirm=lambda req: True, settings=settings, llm=llm,
                            on_event=(events.append if events is not None else None)) as session:
        return await session.send(message), session


async def test_a_failing_turn_is_reported_and_the_session_stays_usable(settings):
    events = []
    trace, session = await _send(settings, ScriptedLLM(), "hello", events=events)  # nothing scripted: router fails
    assert trace.error.startswith("AssertionError")
    assert trace.reply == "Sorry, something went wrong on my side. Please try again."
    assert any(e.type == "error" for e in events)
    assert session.transcript[-1] == ("assistant", trace.reply)


async def test_no_tools_in_scope_skips_the_planner(settings):
    llm = ScriptedLLM(structured=[intent("out_of_scope")], executor=[text("I can only help with insurance.")])
    events = []
    trace, _ = await _send(settings, llm, "tell me a joke", events=events)
    assert trace.selected_tools == [] and trace.plan is None
    assert trace.reply == "I can only help with insurance."
    assert "plan" not in [e.type for e in events]
    assert "none - answer without tools" in llm.create_calls[0]["messages"][-1]["content"]


class PlannerFails(ScriptedLLM):
    async def structured(self, *, system, user, schema, effort):
        if schema is Plan:
            raise StructuredOutputError("truncated")
        return await super().structured(system=system, user=user, schema=schema, effort=effort)


@pytest.mark.xfail(strict=True, reason="BUG: engine._plan returns None on StructuredOutputError, but _run_turn then calls "
                                       "trace.plan.model_dump() on None, so the whole turn fails instead of continuing")
async def test_planner_failure_still_lets_the_executor_answer(settings):
    llm = PlannerFails(structured=[intent("billing")], executor=[text("Your balance is $193.60.")])
    events = []
    trace, _ = await _send(settings, llm, "what do I owe?", events=events)
    assert trace.plan is None and trace.reply == "Your balance is $193.60."
    assert any(e.type == "error" and e.data["message"] == "planner: truncated" for e in events)
    assert "(no plan; decide the steps yourself)" in llm.create_calls[0]["messages"][-1]["content"]


async def test_refusal_gets_a_polite_reply(settings):
    llm = ScriptedLLM(structured=[intent("billing"), plan("respond")], executor=[FakeMessage([], "refusal")])
    trace, _ = await _send(settings, llm, "something odd")
    assert trace.stop_reason == "refusal" and trace.reply == "I'm sorry, I can't help with that request."


async def test_cut_off_answer_without_text(settings):
    llm = ScriptedLLM(structured=[intent("billing"), plan("respond")], executor=[FakeMessage([], "max_tokens")])
    trace, _ = await _send(settings, llm, "explain everything")
    assert trace.reply == "Sorry, my answer was cut off. Could you ask again more briefly?"


async def test_unknown_tool_is_blocked_and_reported_to_the_model(settings):
    llm = ScriptedLLM(structured=[intent("billing"), plan("respond")],
                      executor=[tools(("transfer_funds", {"amount": 5})), text("I can't do that.")])
    trace, _ = await _send(settings, llm, "move money")
    call = trace.tool_calls[0]
    assert call.blocked_reason == "UNKNOWN_TOOL" and call.is_error and not call.executed
    assert call.result == "UNKNOWN_TOOL: transfer_funds does not exist."
    assert llm.create_calls[1]["messages"][-1]["content"][0]["is_error"] is True


async def test_unhandled_intents_without_an_intent(settings):
    async with open_session(customer_id="CUST-001", confirm=lambda r: True, settings=settings,
                            llm=ScriptedLLM()) as session:
        assert session._unhandled_intents(TurnTrace(turn=1, customer_id="CUST-001", user_message="hi")) == []


class SdkBlock:
    def to_dict(self):
        return {"type": "text", "text": "sdk"}


class PydanticLike:
    def model_dump(self, exclude_none=False):
        return {"type": "text", "text": "pydantic"}


@dataclass
class DataBlock:
    type: str = "text"
    text: str = "dataclass"


def test_jsonable_handles_every_block_kind():
    assert engine_mod._jsonable("plain") == "plain"
    out = engine_mod._jsonable([{"type": "text", "text": "dict"}, SdkBlock(), DataBlock(), PydanticLike(), "raw",
                                TextBlock("fake")])
    assert out == [{"type": "text", "text": "dict"}, {"type": "text", "text": "sdk"},
                   {"type": "text", "text": "dataclass"}, {"type": "text", "text": "pydantic"}, "raw",
                   {"text": "fake", "type": "text"}]


def test_passed_reads_only_a_verified_true_result():
    assert engine_mod._passed('{"verified": true}') is True
    assert engine_mod._passed('{"verified": false}') is False
    assert engine_mod._passed("not json") is False
    assert engine_mod._passed("[1, 2]") is False
