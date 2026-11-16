"""Proves the baseline suite catches every planted defect, and passes on the clean build.

    .venv\\Scripts\\python tools\\defect_matrix.py            clean run of the whole suite, then each defect against
                                                            the tests marked catches(<defect>)
    .venv\\Scripts\\python tools\\defect_matrix.py --full     each defect against the whole suite (about 30 minutes)
    .venv\\Scripts\\python tools\\defect_matrix.py phantom_bind no_lockout

The defect list comes from the app itself (policydesk-cli.exe), so a new defect without a test fails the matrix.
Exit code 0: the clean run passed and every defect made at least one test fail. Needs an interactive desktop.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "app" / "bin" / "policydesk-cli.exe"


def known_defects() -> list[str]:
    """The app lists its defects when it refuses an unknown one."""
    out = subprocess.run([str(CLI), "--defects", "?", "--help"], capture_output=True, text=True).stderr
    match = re.search(r"Known: (.+)\.", out)
    if not match:
        raise SystemExit(f"could not read the defect list from {CLI}: {out!r}")
    return [d.strip() for d in match.group(1).split(",")]


def pytest(args: list[str], defects: str) -> tuple[int, str, float]:
    env = {**os.environ, "POLICYDESK_DEFECTS": defects}
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-build", "-p", "no:cacheprovider", *args],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    summary = next((line for line in reversed(proc.stdout.splitlines()) if line.strip()), "")
    return proc.returncode, summary.strip("= "), time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("defects", nargs="*", help="only these defects (default: all the app knows)")
    parser.add_argument("--full", action="store_true", help="run the whole suite against each defect")
    options = parser.parse_args()

    build = subprocess.run(["cmd", "/c", str(ROOT / "app" / "build.bat")], capture_output=True, text=True)
    if build.returncode:
        print(build.stdout, build.stderr)
        return 1
    known = known_defects()
    defects = options.defects or known
    unknown = sorted(set(defects) - set(known))
    if unknown:
        print(f"unknown defects: {', '.join(unknown)}; known: {', '.join(known)}")
        return 2

    ok = True
    code, summary, seconds = pytest([], "")
    print(f"{'clean build':24} {'PASS' if code == 0 else 'FAIL':6} {seconds:6.0f} s  {summary}")
    ok &= code == 0
    for defect in defects:
        code, summary, seconds = pytest([] if options.full else ["--catching", defect], defect)
        if code == 5:                                   # pytest: no tests collected
            verdict, caught = "NO TEST", False
        else:
            caught = code == 1
            verdict = "CAUGHT" if caught else "MISSED"
        ok &= caught
        print(f"{defect:24} {verdict:6} {seconds:6.0f} s  {summary}")
    print("PASSED: the suite passes clean and catches every defect" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
