"""Planted defects, for test runs only: a "buggy build" switched on with ``LAB_DEFECTS=name,name``.

Each defect copies a real class of UI or backend bug, so a test framework can show that its generated scripts
catch it. Production leaves ``LAB_DEFECTS`` unset. Where each defect lives:

- the server: ``discount_ignored`` (a price that disagrees with what the page promises), ``phantom_claim`` (a
  write that reports success but saves nothing), ``upload_drops_last`` (a partial write reported as complete),
  ``board_not_saved`` (a change the page shows but the server forgets);
- the page: ``slow_ui`` (results render later than common default timeouts).
"""

from __future__ import annotations

KNOWN: dict[str, str] = {
    "discount_ignored": "quotes leave out the 10% multi-policy discount the page says was applied",
    "phantom_claim": "a filed claim gets a claim number but is never saved",
    "upload_drops_last": "a multi-file upload reports every file but stores all but the last",
    "board_not_saved": "moving a card on the claims board reports success but is not saved",
    "slow_ui": "the quote result and the policies table render after 8 seconds instead of under 1",
}
# defects the page itself acts on (sent to it by /api/config); the others stay on the server
UI_DEFECTS = frozenset({"slow_ui"})


def parse(value: str | None) -> frozenset[str]:
    """``"a, b"`` -> {"a", "b"}; an unknown name is an error, so a typo never runs a clean build by accident."""
    names = frozenset(n.strip() for n in (value or "").split(",") if n.strip())
    unknown = sorted(names - set(KNOWN))
    if unknown:
        raise ValueError(f"unknown LAB_DEFECTS {unknown}; known: {', '.join(sorted(KNOWN))}")
    return names
