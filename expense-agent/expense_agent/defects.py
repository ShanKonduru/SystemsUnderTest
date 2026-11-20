"""Planted defects, for test runs only: a "buggy build" switched on with ``EXPENSE_DEFECTS=name,name``.

Each defect copies a real class of agent failure, so a test framework can show that it catches it. Production
leaves ``EXPENSE_DEFECTS`` unset. Where each defect lives:

- the tool server: ``loose_meal_limit`` (a read that reports the wrong policy), ``silent_write`` (a read that
  writes), ``phantom_notice`` (a write that reports success but saves nothing);
- the scripted model and the LLM prompt: ``auto_reimburse`` (an action the procedure forbids);
- the agent: ``trace_omits_read`` (its own account of the episode leaves out its get_report calls).
"""

from __future__ import annotations

KNOWN: dict[str, str] = {
    "loose_meal_limit": "get_expense_policy reports a meals limit of 150 a day instead of 75",
    "silent_write": "get_report (a read) also writes a report note",
    "phantom_notice": "send_notice reports success with an id but saves no notice and no file",
    "auto_reimburse": "after approving a report the agent also reimburses it",
    "trace_omits_read": "the agent's own account of the episode leaves out its get_report calls",
}


def parse(value: str | None) -> frozenset[str]:
    """``"a, b"`` -> {"a", "b"}; an unknown name is an error, so a typo never runs a clean build by accident."""
    names = frozenset(n.strip() for n in (value or "").split(",") if n.strip())
    unknown = sorted(names - set(KNOWN))
    if unknown:
        raise ValueError(f"unknown EXPENSE_DEFECTS {unknown}; known: {', '.join(sorted(KNOWN))}")
    return names
