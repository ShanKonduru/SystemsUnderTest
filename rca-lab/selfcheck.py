"""Self-check for the RCA lab: proves the answer key against the billing-service history.

For every ticket in the (private, git-ignored) answer key:

1. the ticket file exists and states the reported version;
2. the culprit line in the key is the actual text of that line at the reported version's tag;
3. the hidden test FAILS at the reported version (in the reporter's environment, where the key gives one);
4. with the reference fix applied (a patch, or the environment corrected), the hidden test PASSES and the shipped
   suite stays green: ruff, pytest with the coverage gate, the portal's node tests;
5. for regressions, the hidden test PASSES at the version that worked.

    billing-service\\.venv\\Scripts\\python rca-lab\\selfcheck.py [--key PATH] [--only T01,T02] [--no-suite]

The key is read from --key, else RCA_LAB_KEY_PATH, else rca-lab/private/answer-key.yaml; hidden tests and fixes are
resolved next to it. Run it with the billing service's virtualenv (it has pytest, ruff and the app's dependencies).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
TICKETS = REPO / "rca-lab" / "tickets"
TAG = "billing-service-v{}"


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[bool, str]:
    full_env = {**os.environ, **(env or {})}
    p = subprocess.run(cmd, cwd=cwd, env=full_env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode == 0, (p.stdout + p.stderr)[-2000:]


class Checkout:
    """One worktree, moved between tags and wiped between checks."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="rca-lab-selfcheck-")) / "wt"
        git("worktree", "add", "-q", "--detach", str(self.root), "HEAD")
        self.billing = self.root / "billing-service"

    def at(self, version: str) -> Checkout:
        git("checkout", "-q", "--force", "--detach", TAG.format(version), cwd=self.root)
        git("clean", "-q", "-fdx", cwd=self.root)
        return self

    def apply(self, patch: Path) -> None:
        git("apply", "--whitespace=nowarn", str(patch), cwd=self.root)

    def close(self) -> None:
        git("worktree", "remove", "--force", str(self.root))
        shutil.rmtree(self.root.parent, ignore_errors=True)


def hidden_test(co: Checkout, spec: dict[str, Any], private: Path, env: dict[str, str]) -> tuple[bool, str]:
    hidden = private / "hidden_tests"
    if spec["kind"] == "pytest":
        return run([sys.executable, "-m", "pytest", "-c", str(hidden / "pytest.ini"), "--rootdir", str(hidden),
                    str(hidden / spec["file"])], co.billing, {"PYTHONPATH": str(co.billing), **env})
    if spec["kind"] == "node":
        return run(["node", "--test", str(hidden / spec["file"])], co.billing,
                   {"PORTAL_SRC": str(co.billing / "portal" / "src"), **env})
    if spec["kind"] == "shipped_alone":
        return run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", spec["test"]], co.billing, env)
    if spec["kind"] == "shipped_repeat":  # the same shipped test, run again in the same checkout
        for n in range(spec.get("runs", 2)):
            ok, out = run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", spec["test"]], co.billing,
                          env)
            if not ok:
                return False, f"run {n + 1}: {out}"
        return True, ""
    raise ValueError(f"unknown hidden test kind {spec['kind']!r}")


def shipped_suite(co: Checkout) -> tuple[bool, str]:
    for cmd, cwd in [
        ([sys.executable, "-m", "ruff", "check", "billing", "tests"], co.billing),
        ([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--cov"], co.billing),
        (["node", "--test", *sorted(f"test/{p.name}" for p in (co.billing / "portal" / "test").glob("*.test.ts"))],
         co.billing / "portal"),
    ]:
        ok, out = run(cmd, cwd)
        if not ok:
            return False, f"{' '.join(cmd[-3:])}: {out}"
    return True, ""


def check_ticket(tid: str, t: dict[str, Any], private: Path, co: Checkout, suite: bool) -> list[str]:
    problems: list[str] = []
    version = t.get("reported_version")

    ticket = TICKETS / f"{t['slug']}.md"
    if not ticket.is_file():
        return [f"ticket file {ticket.name} missing"]
    if version and f"billing service {version}" not in ticket.read_text(encoding="utf-8"):
        problems.append(f"ticket doesn't state version {version}")

    for field in ("culprit", "related"):
        c = t.get(field)
        if not c:
            continue
        lines = git("show", f"{TAG.format(version)}:{c['path']}").splitlines()
        actual = lines[c["line"] - 1] if c["line"] <= len(lines) else None
        if actual != c["text"]:
            problems.append(f"{field} {c['path']}:{c['line']} is {actual!r}, key says {c['text']!r}")

    spec, fix = t.get("hidden_test"), t.get("reference_fix")
    if not spec:
        return problems
    repro_env = fix.get("env_repro", {})
    ok, out = hidden_test(co.at(version), spec, private, repro_env)
    if ok:
        problems.append(f"hidden test passes at {version}: it doesn't reproduce the defect")
    if "patch" in fix:
        co.apply(private / fix["patch"])
        ok, out = hidden_test(co, spec, private, {})
    else:
        ok, out = hidden_test(co, spec, private, fix.get("env_fixed", {}))
    if not ok:
        problems.append(f"hidden test fails after the reference fix:\n{out}")
    if suite and "patch" in fix:
        ok, out = shipped_suite(co)
        if not ok:
            problems.append(f"shipped suite fails after the reference fix: {out}")
    for good in t.get("passes_at", []):
        ok, out = hidden_test(co.at(good), spec, private, {})
        if not ok:
            problems.append(f"hidden test fails at {good}, where it should pass:\n{out}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key", default=os.environ.get("RCA_LAB_KEY_PATH", REPO / "rca-lab" / "private" / "answer-key.yaml"))
    parser.add_argument("--only", help="comma-separated ticket ids")
    parser.add_argument("--no-suite", action="store_true", help="skip the shipped suite after each fix")
    args = parser.parse_args()
    key_path = Path(args.key)
    key = yaml.safe_load(key_path.read_text(encoding="utf-8"))
    wanted = set(args.only.split(",")) if args.only else None

    co = Checkout()
    failures = 0
    try:
        for tid, t in key["tickets"].items():
            if wanted and tid not in wanted:
                continue
            problems = check_ticket(tid, t, key_path.parent, co, not args.no_suite)
            failures += bool(problems)
            print(f"{tid}  {t['slug']:<32} {'ok' if not problems else 'FAIL'}", flush=True)
            for p in problems:
                print("      " + p.replace("\n", "\n      "))
    finally:
        co.close()
    print(f"\n{failures} ticket(s) with problems" if failures else "\nall tickets check out")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
