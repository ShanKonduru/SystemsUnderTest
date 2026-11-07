"""The claims agent's tool loop as a LangGraph graph: a second build of the same agent (``CLAIMS_ENGINE=langgraph``).

Same prompts, same tools, same model turn and tool step as the plain loop in ``agent.py``; only the orchestration
differs: a ``model`` node and a ``tools`` node, with an edge back to the model while it asks for tools, and the
step limit as graph state. A test framework can run both builds and judge their parity (SE-06).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, START, StateGraph

if TYPE_CHECKING:
    from claims_agent.agent import ClaimsAgent, EpisodeResult, ToolSession
    from claims_agent.telemetry import EpisodeSpans


class State(TypedDict):
    turns: int
    uses: list[Any]


async def run_graph(
    agent: ClaimsAgent,
    ep: EpisodeResult,
    tools: ToolSession,
    specs: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    spans: EpisodeSpans,
) -> None:
    limit = agent.settings.max_steps

    async def model(state: State) -> dict[str, Any]:
        return {"turns": state["turns"] + 1, "uses": await agent.model_turn(ep, specs, messages)}

    async def act(state: State) -> dict[str, Any]:
        messages.append({"role": "user", "content": await agent.run_tools(ep, tools, state["uses"], spans)})
        return {}

    def after_model(state: State) -> str:
        if not state["uses"]:
            return END
        return "tools" if state["turns"] < limit else "limit"

    def at_limit(state: State) -> dict[str, Any]:
        return {}

    graph = StateGraph(State)
    graph.add_node("model", model)
    graph.add_node("tools", act)
    graph.add_node("limit", at_limit)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model, {"tools": "tools", "limit": "limit", END: END})
    graph.add_edge("tools", "model")
    graph.add_edge("limit", END)
    final = await graph.compile().ainvoke({"turns": 0, "uses": []}, config={"recursion_limit": 2 * limit + 4})
    if final["uses"]:  # stopped at the step limit with tool calls still pending
        await agent.run_tools(ep, tools, final["uses"], spans)
        ep.outcome, ep.done = "max_steps", False
