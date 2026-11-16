"""The whole app: launching it, its main window, the sign-in dialog and the status bar."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pywinauto import Application

from .base import MessageBox, Screen, until
from .quote import QuoteScreen
from .quotes import QuotesScreen
from .record import Record


class LoginScreen(Screen):
    def sign_in(self, user: str, password: str, office: str | None = None) -> None:
        self.set_text("txtUser", user)
        self.set_text("txtPassword", password)
        if office:
            self.choose("cboOffice", office)
        self.press("btnSignIn")

    @property
    def error(self) -> str:
        return self.text("lblError")


class PolicyDesk:
    """One launch of PolicyDesk.exe from seed data, in its own data folder."""

    def __init__(self, exe: Path, data_dir: Path, export_dir: Path, *, release: str = "v1",
                 latency_ms: int = 300, extra: tuple[str, ...] = ()) -> None:
        self.exe = exe
        self.data_dir = data_dir
        self.export_dir = export_dir
        self.args = [str(exe), "--reset", "--data", str(data_dir), "--release", release,
                     "--latency-ms", str(latency_ms), "--export-dir", str(export_dir), *extra]
        self.release = release
        self.app: Application | None = None
        self.record = Record(data_dir)

    def start(self) -> PolicyDesk:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.app = Application(backend="uia").start(subprocess.list2cmdline(self.args), timeout=30)
        self.main = self.app.window(auto_id="MainForm")
        if not self.main.exists(timeout=20):
            raise LookupError("no window with AutomationId 'MainForm': the suite's locators are release v1's, "
                              f"and release {self.release} renames them")
        return self

    def stop(self) -> None:
        if self.app is not None and self.app.is_process_running():
            self.app.kill()

    # ── windows and screens ─────────────────────────────────────────────────
    @property
    def login(self) -> LoginScreen:
        spec = self.main.child_window(auto_id="LoginForm")
        spec.wait("exists", timeout=15)
        return LoginScreen(spec)

    def sign_in(self, user: str = "tester", password: str = "harbor", office: str | None = None) -> QuoteScreen:
        self.login.sign_in(user, password, office)
        until(lambda: not self.main.child_window(auto_id="LoginForm").exists(timeout=0), "the sign-in dialog to close")
        return self.quote

    @property
    def quote(self) -> QuoteScreen:
        return QuoteScreen(self.main)

    @property
    def quotes(self) -> QuotesScreen:
        return QuotesScreen(self.main).open()

    def message_box(self, title: str = "Policy Desk") -> MessageBox:
        return MessageBox(self.main.child_window(title=title, control_type="Window"))

    # ── the status bar: its texts have no AutomationId, so read them through the bar ──
    def status_texts(self) -> list[str]:
        bar = Screen(self.main).find("statusMain")
        return [t.window_text() for t in bar.children(control_type="Text")]

    def status(self) -> str:
        return self.status_texts()[0]

    def wait_status(self, expected: str) -> str:
        return until(lambda: self.status() == expected and expected, f"status '{expected}'")

    def close(self) -> None:
        """Closes the main window the way a user would (the title bar's Close)."""
        self.main.wrapper_object().close()
