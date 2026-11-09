"""Planted defects, for test runs only: a "buggy build" switched on with ``DESK_DEFECTS=name,name``.

Each copies a real class of multi-agent failure, so a test framework can show it catches it. Production leaves
``DESK_DEFECTS`` unset. Where each defect lives:

- the scripted model: ``handoff_order`` (triage asks claims before billing for a two-part question),
  ``no_containment`` (billing hands off to claims after its own lookup failed, and opens no case);
- the agents: ``wrong_member`` (billing delegates to claims for a different member), ``no_narrowing`` (handoffs
  keep every permission instead of narrowing to the receiving role's), ``trace_omits_hop`` (triage's account
  leaves out the handoffs its specialists made).
"""

from __future__ import annotations

KNOWN: dict[str, str] = {
    "handoff_order": "triage hands a two-part question to claims first, then to billing",
    "no_containment": "billing hands off to claims after its own lookup failed, and opens no case",
    "wrong_member": "billing delegates to claims for a different member",
    "no_narrowing": "handoffs keep every permission instead of narrowing to the receiving role's",
    "trace_omits_hop": "triage's account leaves out the handoffs its specialists made",
}


def parse(value: str | None) -> frozenset[str]:
    """``"a, b"`` -> {"a", "b"}; an unknown name is an error, so a typo never runs a clean build by accident."""
    names = frozenset(n.strip() for n in (value or "").split(",") if n.strip())
    unknown = sorted(names - set(KNOWN))
    if unknown:
        raise ValueError(f"unknown DESK_DEFECTS {unknown}; known: {', '.join(sorted(KNOWN))}")
    return names
