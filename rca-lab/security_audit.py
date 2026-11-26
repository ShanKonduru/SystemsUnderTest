"""Dependency security audit of the billing service, with the sec-utils scanners, as one HTML report.

    python rca-lab\\security_audit.py                         :: the working tree's billing-service
    python rca-lab\\security_audit.py --ref billing-service-v1.3.0
    python rca-lab\\security_audit.py --ref rca/fix-branch --out C:\\reports\\fix
    python rca-lab\\security_audit.py --offline               :: trivy only (pip-audit needs PyPI and OSV)

Scanners, all from the sec-utils bundle (--sec-utils, else SEC_UTILS, else a `sec-utils` folder next to this repo):

- trivy, offline, with the bundle's vulnerability DB: `requirements.txt`;
- pip-audit: `requirements.txt` and the project's `pyproject.toml` (each resolved with its dependencies);
- sec-report-kit (`srk render consolidated`): one HTML report of everything found.

The audit is clean when no scanner reports a vulnerability. Exit code 0 = clean, 1 = vulnerabilities found,
2 = a scanner could not run. The JSON results and the report go to --out (default rca-lab/audit-reports/<label>/,
git-ignored).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = "billing-service"


@dataclass(frozen=True)
class Finding:
    scanner: str
    source: str
    package: str
    version: str
    vuln_id: str
    severity: str
    fixed: str


class Tools:
    def __init__(self, root: Path) -> None:
        self.root = root
        scripts = root / "venv" / ("Scripts" if os.name == "nt" else "bin")
        self.trivy = self._find(root / "trivy" / ("trivy.exe" if os.name == "nt" else "trivy"), "trivy")
        self.trivy_cache = root / "trivy" / "cache"
        self.pip_audit = self._find(scripts / ("pip-audit.exe" if os.name == "nt" else "pip-audit"), "pip-audit")
        self.srk = self._find(scripts / ("srk.exe" if os.name == "nt" else "srk"), "srk")

    @staticmethod
    def _find(bundled: Path, name: str) -> str:
        if bundled.is_file():
            return str(bundled)
        found = shutil.which(name)
        if not found:
            sys.exit(f"{name} not found in {bundled.parent} or on PATH: point --sec-utils at the sec-utils bundle")
        return found


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def trivy(tools: Tools, app: Path, out: Path) -> list[Finding]:
    report = out / "trivy.json"
    p = run([tools.trivy, "fs", "--cache-dir", str(tools.trivy_cache), "--offline-scan", "--skip-db-update",
             "--skip-check-update", "--scanners", "vuln", "--format", "json", "--output", str(report), "."], app)
    if p.returncode != 0 or not report.is_file():
        raise RuntimeError(f"trivy failed:\n{p.stdout}{p.stderr}")
    findings = []
    for result in json.loads(report.read_text(encoding="utf-8")).get("Results") or []:
        for v in result.get("Vulnerabilities") or []:
            findings.append(Finding("trivy", result["Target"], v["PkgName"], v["InstalledVersion"],
                                    v["VulnerabilityID"], v.get("Severity", "UNKNOWN"), v.get("FixedVersion", "")))
    return findings


def pip_audit(tools: Tools, app: Path, out: Path, source: str) -> list[Finding]:
    report = out / f"pip-audit-{Path(source).stem}.json"
    target = ["-r", source] if source.endswith(".txt") else ["."]
    p = run([tools.pip_audit, *target, "--format", "json", "--output", str(report), "--progress-spinner", "off",
             "--aliases", "on"], app)
    # pip-audit exits 1 when it finds vulnerabilities; anything without a report is a failure to run
    if not report.is_file():
        raise RuntimeError(f"pip-audit {source} failed:\n{p.stdout}{p.stderr}")
    findings = []
    for dep in json.loads(report.read_text(encoding="utf-8")).get("dependencies", []):
        for v in dep.get("vulns") or []:
            findings.append(Finding("pip-audit", source, dep["name"], dep["version"], v["id"], "",
                                    ", ".join(v.get("fix_versions") or [])))
    return findings


def render(tools: Tools, out: Path, label: str) -> Path | None:
    p = run([tools.srk, "render", "consolidated", "--input", str(out), "--output", str(out), "--target", label], out)
    reports = sorted(out.glob("*.html"), key=lambda f: f.stat().st_mtime, reverse=True)
    if p.returncode != 0 or not reports:
        print(f"warning: srk could not render the report:\n{p.stdout}{p.stderr}", file=sys.stderr)
        return None
    return reports[0]


def summary(findings: list[Finding]) -> str:
    rows: dict[tuple[str, str, str, str], list[Finding]] = {}
    for f in findings:
        rows.setdefault((f.scanner, f.source, f.package, f.version), []).append(f)
    lines = [f"{'scanner':<10} {'file':<18} {'package':<12} {'version':<10} {'vulns':>5}  worst     fixed in"]
    order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN", ""]
    for (scanner, source, package, version), fs in sorted(rows.items()):
        worst = min((f.severity for f in fs), key=order.index) or "-"
        fixed = max((f.fixed.split(",")[-1].strip() for f in fs if f.fixed), default="", key=_version_key)
        lines.append(f"{scanner:<10} {source:<18} {package:<12} {version:<10} {len(fs):>5}  {worst:<9} {fixed}")
    return "\n".join(lines)


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in v.split("."))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--ref", help="git ref to audit (tag, branch or commit), checked out in a temporary worktree")
    where.add_argument("--path", type=Path, help="a billing-service folder to audit (default: the working tree's)")
    parser.add_argument("--out", type=Path, help="folder for the JSON results and the HTML report")
    parser.add_argument("--sec-utils", type=Path,
                        default=Path(os.environ.get("SEC_UTILS") or REPO.parent / "sec-utils"))
    parser.add_argument("--offline", action="store_true", help="skip pip-audit, which needs PyPI and OSV")
    args = parser.parse_args()

    tools = Tools(args.sec_utils)
    label = (args.ref or "working-tree").replace("/", "-")
    out = (args.out or REPO / "rca-lab" / "audit-reports" / label).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    worktree = None
    try:
        if args.ref:
            worktree = Path(tempfile.mkdtemp(prefix="security-audit-")) / "wt"
            subprocess.run(["git", "worktree", "add", "-q", "--detach", str(worktree), args.ref], cwd=REPO, check=True)
            app = worktree / APP
        else:
            app = (args.path or REPO / APP).resolve()
        print(f"auditing {app} ({label})", flush=True)
        try:
            findings = trivy(tools, app, out)
            if not args.offline:
                findings += pip_audit(tools, app, out, "requirements.txt")
                findings += pip_audit(tools, app, out, "pyproject.toml")
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 2
    finally:
        if worktree:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO, check=False)
            shutil.rmtree(worktree.parent, ignore_errors=True)

    report = render(tools, out, f"{APP} {label}")
    scanners = "trivy" if args.offline else "trivy, pip-audit"
    if findings:
        print(f"\n{len(findings)} vulnerabilities found ({scanners}):\n\n{summary(findings)}")
    else:
        print(f"\nclean: no known vulnerabilities ({scanners})")
    print(f"\nresults in {out}" + (f"\nreport: {report}" if report else ""))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
