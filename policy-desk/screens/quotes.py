"""The Quotes tab (filter, grid, open, edit, export) and the dialogs it opens."""

from __future__ import annotations

from pathlib import Path

from pywinauto.application import WindowSpecification

from .base import Screen, until


class DetailsDialog(Screen):
    """Quote <n>: read-only fields, Bind policy, Decline..., Close."""

    def field(self, auto_id: str) -> str:
        return self.value(auto_id)

    def bind(self) -> None:
        self.press("btnBind")
        until(lambda: not self.root.exists(timeout=0), "the quote dialog to close")

    def decline(self, reason: str, note: str = "") -> None:
        self.click("btnDecline")                       # opens a modal on top of this modal
        dialog = Screen(self.root.child_window(auto_id="DeclineForm"))
        dialog.choose("cboReason", reason)
        if note:
            dialog.set_text("txtDeclineNote", note)
        dialog.press("btnConfirmDecline")
        until(lambda: not self.root.exists(timeout=0), "the quote dialog to close")

    def close(self) -> None:
        self.press("btnClose")
        until(lambda: not self.root.exists(timeout=0), "the quote dialog to close")


class QuotesScreen(Screen):
    def __init__(self, main: WindowSpecification) -> None:
        super().__init__(main)
        self.main = main

    def open(self) -> QuotesScreen:
        tab = self.root.child_window(title="Quotes", control_type="TabItem")
        tab.wrapper_object().select()
        self.find("dgvQuotes")
        return self

    def rows(self) -> list[dict[str, str]]:
        """The visible rows of the grid, as {column header: cell text}."""
        grid = self.find("dgvQuotes")
        rows = []
        for row in grid.children(control_type="Custom"):
            if not row.window_text().startswith("Row "):
                continue                                # the header row is "Top Row"
            cells = {}
            for cell in row.children(control_type="DataItem"):
                header = cell.element_info.name.rsplit(" Row ", 1)[0]
                cells[header] = cell.iface_value.CurrentValue
            rows.append(cells)
        return rows

    def row(self, number: int) -> dict[str, str] | None:
        return next((r for r in self.rows() if r.get("Quote") == str(number)), None)

    def find_quotes(self, text: str = "", status: str = "All") -> list[dict[str, str]]:
        self.set_text("txtFilter", text)
        self.choose("cboStatusFilter", status)
        self.press("btnFind")
        return self.rows()

    def select(self, number: int) -> None:
        grid = self.find("dgvQuotes")
        cell = until(lambda: next((c for c in grid.descendants(control_type="DataItem")
                                   if c.element_info.name.startswith("Quote Row ")
                                   and c.iface_value.CurrentValue == str(number)), None), f"quote {number} in the grid")
        cell.click_input()

    def open_quote(self, number: int) -> DetailsDialog:
        self.select(number)
        self.click("btnOpenQuote")                     # opens a modal: click with the mouse
        spec = self.main.child_window(auto_id="QuoteDetailsForm")
        spec.wait("exists", timeout=10)
        return DetailsDialog(spec)

    def edit_quote(self, number: int) -> None:
        self.select(number)
        self.click("btnEditQuote")                     # may show a message box, so click with the mouse

    def export(self, path: Path) -> None:
        """Export... opens the Windows Save File dialog; type the full path and save."""
        self.click("btnExport")
        dialog = self.main.child_window(title="Export quotes", control_type="Window")
        dialog.wait("exists", timeout=10)
        dialog.child_window(auto_id="1001", control_type="Edit").wrapper_object().set_edit_text(str(path))
        dialog.child_window(title="Save", control_type="Button").wrapper_object().invoke()
        until(lambda: not dialog.exists(timeout=0), "the Save dialog to close")
