"""What every screen object shares: finding controls, waiting for states, and acting on controls the way that works.

Locators are the v1 AutomationIds, the way a recorded script would have them. The baseline suite therefore passes on
release v1 and breaks on v2-v4: that is the starting point self-healing is measured from.

Three rules of desktop automation the helpers encode (see the README's "traps"):
- wait for a state, never sleep a fixed time (``until``);
- click with the mouse anything that opens a modal dialog: the UI Automation Invoke pattern blocks until it closes;
- choose drop-down items with the keyboard: a selection set through UI Automation fires no change event.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from pywinauto.application import WindowSpecification
from pywinauto.base_wrapper import BaseWrapper

DEFAULT_TIMEOUT = 10.0
UNDERWRITING_TIMEOUT = 5.0  # the suite launches with --latency-ms 300; anything near 5 s is a regression

T = TypeVar("T")


def until(probe: Callable[[], T], what: str, timeout: float = DEFAULT_TIMEOUT, interval: float = 0.1) -> T:
    """Polls ``probe`` until it returns something truthy.

    Errors while the UI settles count as "not yet"; an AssertionError from the probe ends the wait at once.
    """
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while True:
        try:
            result = probe()
            if result:
                return result
        except AssertionError:
            raise                      # a verdict, not "not yet"
        except Exception as exc:  # noqa: BLE001 - a control that is not there yet raises; keep waiting
            last = exc
        if time.monotonic() > deadline:
            detail = f" (last error: {last})" if last else ""
            raise TimeoutError(f"timed out after {timeout:g} s waiting for {what}{detail}")
        time.sleep(interval)


class Screen:
    """A window or a region of one, located by a pywinauto window specification."""

    def __init__(self, root: WindowSpecification) -> None:
        self.root = root

    def find(self, auto_id: str, timeout: float = DEFAULT_TIMEOUT) -> BaseWrapper:
        spec = self.root.child_window(auto_id=auto_id)
        return until(spec.wrapper_object, f"control '{auto_id}'", timeout)

    def exists(self, auto_id: str) -> bool:
        return self.root.child_window(auto_id=auto_id).exists(timeout=0)

    def text(self, auto_id: str) -> str:
        return self.find(auto_id).window_text()

    def value(self, auto_id: str) -> str:
        return self.find(auto_id).iface_value.CurrentValue

    def name(self, auto_id: str) -> str:
        """The UI Automation Name: for a text box, its label."""
        return self.find(auto_id).element_info.name

    def enabled(self, auto_id: str) -> bool:
        return self.find(auto_id).is_enabled()

    def set_text(self, auto_id: str, text: str) -> None:
        self.find(auto_id).set_edit_text(text)

    def choose(self, auto_id: str, item: str) -> None:
        """Picks a drop-down item with the keyboard, so the app sees a real selection change."""
        box = self.find(auto_id)
        if box.iface_value.CurrentValue == item:
            return
        box.set_focus()
        box.type_keys("{HOME}")
        for _ in range(20):
            if box.iface_value.CurrentValue == item:
                return
            box.type_keys("{DOWN}")
        raise AssertionError(f"'{item}' is not an item of {auto_id}")

    def set_checked(self, auto_id: str, checked: bool) -> None:
        box = self.find(auto_id)
        if bool(box.get_toggle_state()) != checked:
            box.invoke()

    def press(self, auto_id: str) -> None:
        """Clicks a button that does not open a modal dialog (UI Automation Invoke)."""
        button = self.find(auto_id)
        until(button.is_enabled, f"'{auto_id}' to be enabled")
        button.invoke()

    def click(self, auto_id: str) -> None:
        """Clicks with the mouse: for anything that opens a modal dialog."""
        button = self.find(auto_id)
        until(button.is_enabled, f"'{auto_id}' to be enabled")
        button.click_input()

    def wait_text(self, auto_id: str, predicate: Callable[[str], bool], what: str,
                  timeout: float = DEFAULT_TIMEOUT) -> str:
        return until(lambda: (lambda t: t if predicate(t) else None)(self.text(auto_id)), what, timeout)


class MessageBox:
    """A Win32 message box (#32770). Policy Desk titles its own "Policy Desk"."""

    def __init__(self, spec: WindowSpecification) -> None:
        self.spec = spec
        self.window = until(spec.wrapper_object, f"message box '{spec.criteria[-1].get('title')}'")

    @property
    def text(self) -> str:
        return "\n".join(t.window_text() for t in self.window.descendants(control_type="Text") if t.window_text())

    def press(self, button: str) -> None:
        self.spec.child_window(title=button, control_type="Button").wrapper_object().invoke()
        until(lambda: not self.spec.exists(timeout=0), "the message box to close")
