"""The New quote tab: entering a quote, pricing it, checking underwriting and saving it."""

from __future__ import annotations

from .base import UNDERWRITING_TIMEOUT, Screen, until

# Each labelled field and the UI Automation Name its label gives it.
LABELS = {
    "txtPolicyholder": "Policyholder:", "cboCustomerType": "Customer type:", "txtEmail": "Email:",
    "mtbPhone": "Phone:", "cboProduct": "Product:", "cboCoverage": "Coverage:", "cboDeductible": "Deductible:",
}


class QuoteScreen(Screen):
    def open(self) -> QuoteScreen:
        tab = self.root.child_window(control_type="TabItem", found_index=0)
        tab.wrapper_object().select()
        return self

    def fill(self, *, policyholder: str | None = None, customer_type: str | None = None, email: str | None = None,
             phone: str | None = None, product: str | None = None, coverage: str | None = None,
             deductible: int | None = None, start: str | None = None, roadside: bool | None = None,
             multi_policy: bool | None = None, monthly: bool | None = None) -> QuoteScreen:
        """Fills the given fields in a user's order: Product before Roadside, which only Auto allows."""
        if policyholder is not None:
            self.set_text("txtPolicyholder", policyholder)
        if customer_type is not None:
            self.choose("cboCustomerType", customer_type)
        if email is not None:
            self.set_text("txtEmail", email)
        if phone is not None:
            self.set_phone(phone)
        if product is not None:
            self.choose("cboProduct", product)
        if coverage is not None:
            self.choose("cboCoverage", coverage)
        if deductible is not None:
            self.choose("cboDeductible", str(deductible))
        if start is not None:
            self.set_start(start)
        if roadside is not None:
            self.set_checked("chkRoadside", roadside)
        if multi_policy is not None:
            self.set_checked("chkMultiPolicy", multi_policy)
        if monthly is not None:
            self.press("rbMonthly" if monthly else "rbAnnual")
        return self

    def set_phone(self, digits: str) -> None:
        """A masked box: type the digits, the mask adds the brackets and the dash."""
        box = self.find("mtbPhone")
        box.set_focus()
        box.type_keys("^a{BACKSPACE}" + digits, pause=0.02)

    def set_start(self, iso_date: str) -> None:
        """The date picker is a Pane whose Name is the date it shows; type year, month and day."""
        year, month, day = iso_date.split("-")
        picker = self.find("dtpStart")
        picker.set_focus()
        picker.type_keys("{LEFT}{LEFT}{LEFT}" + year + "{RIGHT}" + month + "{RIGHT}" + day, pause=0.02)
        until(lambda: picker.window_text() == iso_date, f"the start date to read {iso_date}")

    # ── results ──────────────────────────────────────────────────────────────
    def calculate(self) -> str:
        self.press("btnCalculate")
        return self.premium

    @property
    def premium(self) -> str:
        return self.text("lblPremium")

    @property
    def installment(self) -> str:
        return self.text("lblInstallment")

    @property
    def underwriting(self) -> str:
        return self.text("lblUnderwriting")

    @property
    def errors(self) -> list[str]:
        text = self.text("lblErrors")
        # An empty WinForms label reports the previous label's text as its Name (here the underwriting label).
        if text == self.underwriting:
            return []
        return [line for line in text.splitlines() if line]

    def check_underwriting(self, timeout: float = UNDERWRITING_TIMEOUT) -> str:
        """Starts the check and waits for its result (never a fixed sleep)."""
        self.press("btnCheck")
        done = ("Underwriting: Approved", "Underwriting: Referred")

        def outcome() -> str | None:
            result = self.underwriting
            if result.startswith(done):
                return result
            if self.errors:                        # validation refused the form: say so now, not after a timeout
                raise AssertionError(f"underwriting was not run; the form was refused: {self.errors}")
            return None

        return until(outcome, "an underwriting result", timeout)

    def check_expecting_errors(self) -> list[str]:
        self.press("btnCheck")
        return until(lambda: self.errors, "validation messages")

    def save(self) -> None:
        self.press("btnSave")

    def save_from_toolbar(self) -> None:
        """The icon-only toolbar's second button: no name, no text, so it is found by position."""
        toolbar = self.find("toolbarMain")
        toolbar.children(control_type="Button")[1].click_input()
