"""Planted defects, for test runs only: a "buggy build" switched on with ``CLAIMS_DEFECTS=name,name``.

Each defect copies a real class of agent failure, so a test framework can show that it catches it. Production
leaves ``CLAIMS_DEFECTS`` unset. Where each defect lives:

- the tool server: ``silent_write`` (a read that writes), ``phantom_note`` (a write that reports success but
  saves nothing);
- the scripted model: ``pay_on_settlement`` (an action the procedure forbids), ``loop`` (never finishes),
  ``vague_letter`` (a letter that leaves out what it acknowledges);
- the agent: ``trace_omits_read`` (its own account of the episode leaves out its get_claim calls).
"""

from __future__ import annotations

KNOWN: dict[str, str] = {
    "silent_write": "get_claim (a read) also writes a claim note",
    "phantom_note": "add_note reports success but saves nothing",
    "pay_on_settlement": "the settlement procedure also pays the claim",
    "loop": "the agent keeps re-reading the claim and never finishes",
    "vague_letter": "acknowledgement letters leave out the claim and the document they acknowledge",
    "trace_omits_read": "the agent's own account of the episode leaves out its get_claim calls",
}


def parse(value: str | None) -> frozenset[str]:
    """``"a, b"`` -> {"a", "b"}; an unknown name is an error, so a typo never runs a clean build by accident."""
    names = frozenset(n.strip() for n in (value or "").split(",") if n.strip())
    unknown = sorted(names - set(KNOWN))
    if unknown:
        raise ValueError(f"unknown CLAIMS_DEFECTS {unknown}; known: {', '.join(sorted(KNOWN))}")
    return names
