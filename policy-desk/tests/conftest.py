"""Fixtures: build the app once, then launch it fresh from seed data for every test, in its own folders.

Planted defects reach the app through the environment (POLICYDESK_DEFECTS), which the app reads itself; the suite
never looks at them. tools\\defect_matrix.py uses that to run the suite against each buggy build.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screens import PolicyDesk, QuoteScreen  # noqa: E402

EXE = ROOT / "app" / "bin" / "PolicyDesk.exe"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--release", default="v1", help="UI release to launch (the suite's locators are v1's)")
    parser.addoption("--no-build", action="store_true", help="use the existing app\\bin\\PolicyDesk.exe")
    parser.addoption("--catching", metavar="DEFECT", help="run only the tests marked catches(DEFECT)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    defect = config.getoption("--catching")
    if not defect:
        return
    keep = [i for i in items if any(m.args == (defect,) for m in i.iter_markers("catches"))]
    config.hook.pytest_deselected(items=[i for i in items if i not in keep])
    items[:] = keep


@pytest.fixture(scope="session")
def exe(request: pytest.FixtureRequest) -> Path:
    if not request.config.getoption("--no-build"):
        subprocess.run(["cmd", "/c", str(ROOT / "app" / "build.bat")], check=True, capture_output=True)
    assert EXE.exists(), f"{EXE} is missing: run app\\build.bat"
    return EXE


@pytest.fixture
def desk(exe: Path, tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[PolicyDesk]:
    app = PolicyDesk(exe, tmp_path / "data", tmp_path / "export", release=request.config.getoption("--release"))
    try:
        app.start()        # inside the try: a launch that fails (e.g. on v2) must not leave the app running
        yield app
    finally:
        app.stop()


@pytest.fixture
def quote(desk: PolicyDesk) -> QuoteScreen:
    """Signed in as tester (Boston), on the New quote tab."""
    return desk.sign_in()


def pytest_report_header(config: pytest.Config) -> str:
    defects = os.environ.get("POLICYDESK_DEFECTS") or "none"
    return f"Policy Desk {EXE} release={config.getoption('--release')} defects={defects}"
