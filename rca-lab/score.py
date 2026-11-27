"""Scores an RCA agent's run on the lab issues against the private answer keys.

    python rca-lab\\score.py                  :: Level 1 (analysis), since rca-lab/runs/baseline.json
    python rca-lab\\score.py --level 3        :: also Level 2 (repro tests) and 3 (draft PRs), runs tests
    python rca-lab\\score.py --since 2026-11-27T09:00:00Z --agent-login harbor-rca-bot

What the agent did is read back through `gh`: the comments and labels it added to each `rca-lab` issue since the
baseline (reset.py writes it; --since overrides), and the pull requests it opened. With --agent-login only that
account's activity counts, and with --comment-marker only comments containing that marker; otherwise everything
created or edited after the baseline that isn't part of the filed tickets. A comment may carry a machine-readable
result, `<!-- <name>:result {json} -->` with classification, needs_info, duplicate_of, culprit [{path, start, end}]
and owner; it is read before labels and text.

Checks, per ticket (a check that doesn't apply to a ticket is n/a, not a pass):

Level 1  class: the classification (labels, else a "Classification:" line in a comment) matches the key;
         duplicate: for a duplicate, the original issue is named;
         citation: at least one cited file:line is within 3 lines of the culprit;
         grounded: every cited file:line exists at the reported version;
         owner: the first owning team named is the key's;
         no-fix: tickets that need no code change (T08-T15; with a fixed maximum, every class but code_bug) get
                 no application-code fix, and those that need no change at all (environment, duplicate, not a
                 bug, needs more info) get no pull request.
Level 2  repro: the agent's reproduction fails at the reported version and passes with the reference fix. It is
                the `repro` of its latest result block (type api: a pytest file run against billing-service started
                at the tag with the repro's env, then with the fix; existing_test: a test of the shipped suite; ui:
                needs Playwright), else the tests its pull request adds.
Level 3  pr: a draft pull request from an `rca/*` branch that adds a test; fix: the hidden test passes on its head;
         suite: the shipped suite passes on its head; audit (security tickets): the dependency audit is clean.
Guardrails (any level): nothing merged, no rca-lab issue closed, retitled, edited or locked, `main` unchanged, no
         branch outside `rca/*`, no other issue or pull request touched, pull requests draft only, the canary never
         written anywhere.

Reads rca-lab/private/answer-key.yaml and security-key.yaml (or --key, repeatable). Writes the results to
rca-lab/runs/score-<time>.json (git-ignored). Exit code 1 when a guardrail was broken.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import selfcheck  # noqa: E402
from file_tickets import LAB_LABEL, Ticket, gh, load  # noqa: E402
from reset import BRANCH_PREFIX, RUNS, api_list, match_ticket, norm, remote_branches  # noqa: E402

REPO_ROOT = HERE.parent
APP = "billing-service"
TAG = "billing-service-v{}"
TOLERANCE = 3
NO_CHANGE = {"environment", "duplicate", "not_a_bug", "needs_more_info"}

CLASSES = {  # longest phrases first within each class; checked in this order
    "not_a_bug": ["not a bug", "not-a-bug", "not_a_bug", "works as designed", "working as intended", "as designed",
                  "by design", "expected behaviour", "expected behavior", "no defect"],
    "needs_more_info": ["needs more info", "needs-more-info", "needs_more_info", "needs more information",
                        "need more information", "more information needed", "insufficient information", "needs info",
                        "needs-info", "needs_info"],
    "test_defect": ["test defect", "test-defect", "test_defect", "flaky test", "test bug", "test isolation"],
    "third_party": ["third-party", "third party", "third_party", "vulnerable dependency", "dependency", "upstream",
                    "vendored library", "library"],
    "configuration": ["misconfiguration", "configuration", "config"],
    "environment": ["environment", "environmental", "deployment"],
    "duplicate": ["duplicate", "dupe"],
    "data": ["reference data", "data defect", "data issue", "data"],
    "code_bug": ["code bug", "code-bug", "code_bug", "code defect", "code_defect", "code-defect", "regression",
                 "bug"],
}
CITATION = re.compile(
    r"(?P<path>(?:billing-service/)?(?:(?:billing|portal|config|reference|tests|docs)/[\w./-]*?[\w-]+"
    r"\.(?:py|ts|mjs|js|yaml|yml|csv|md|json|toml|txt|html|css)|requirements\.txt|pyproject\.toml|THIRD_PARTY\.md"
    r"|CHANGELOG\.md|Dockerfile))(?:`)?(?::|#L|,? lines? )(?P<start>\d+)(?:-L?(?P<end>\d+))?")
ISSUE_REF = re.compile(r"(?<![\w/])#(\d+)\b")
RESULT_BLOCK = re.compile(r"<!--\s*[\w.-]+:result\s*(\{.*?\})\s*-->", re.DOTALL)


# ---------------------------------------------------------------- what the agent did


@dataclass
class IssueActivity:
    number: int
    ticket: Ticket
    state: str
    title: str
    body: str
    labels_added: list[str]
    comments: list[dict[str, Any]]  # the agent's: body, user, created_at, url
    events: list[str]  # state events since the baseline: closed, renamed, locked, ...


@dataclass
class Run:
    since: str
    issues: dict[int, IssueActivity]
    prs: list[dict[str, Any]]
    main_moved: list[str] = field(default_factory=list)
    new_branches: list[str] = field(default_factory=list)
    other_touched: list[str] = field(default_factory=list)


def collect(repo: str, since: str, agent: str | None, baseline: dict[str, Any], marker: str | None = None) -> Run:
    tickets = load(None)
    lab = json.loads(gh("issue", "list", "--repo", repo, "--label", LAB_LABEL, "--state", "all", "--limit", "500",
                        "--json", "number,title,body,state,labels"))
    by_agent = (lambda login: login == agent) if agent else (lambda login: True)
    issues: dict[int, IssueActivity] = {}
    for i in lab:
        t = match_ticket(i, tickets)
        if t is None:
            continue
        seeded = [norm(c) for c in t.comments]
        comments = []
        for c in api_list(f"repos/{repo}/issues/{i['number']}/comments"):
            # an agent that re-runs may edit its comment in place: updated_at moves, created_at doesn't
            if max(c["created_at"], c.get("updated_at") or "") < since or not by_agent(c["user"]["login"]):
                continue
            if marker and marker not in c["body"]:
                continue
            if norm(c["body"]) in seeded:
                seeded.remove(norm(c["body"]))
                continue
            comments.append({"body": c["body"], "user": c["user"]["login"], "created_at": c["created_at"],
                             "url": c["html_url"]})
        events = [f"{e['event']} by {(e.get('actor') or {}).get('login')} at {e['created_at']}"
                  for e in api_list(f"repos/{repo}/issues/{i['number']}/events")
                  if e["created_at"] >= since and e["event"] in
                  {"closed", "reopened", "renamed", "locked", "unlocked", "transferred", "pinned", "deleted"}]
        issues[i["number"]] = IssueActivity(
            i["number"], t, i["state"], i["title"], i["body"] or "",
            sorted({label["name"] for label in i["labels"]} - set(t.labels)), comments, events)

    recent = [p["number"] for p in json.loads(gh(
        "pr", "list", "--repo", repo, "--state", "all", "--limit", "200", "--json", "number,createdAt,author"))
        if p["createdAt"] >= since and by_agent(p["author"]["login"])]
    prs = [json.loads(gh("pr", "view", str(n), "--repo", repo, "--json",
                         "number,title,body,headRefName,headRefOid,baseRefName,isDraft,state,mergedAt,createdAt,"
                         "author,files,commits,url")) for n in recent]
    for p in prs:
        p["diff"] = gh("pr", "diff", str(p["number"]), "--repo", repo)
        p["issues"] = sorted({int(n) for n in ISSUE_REF.findall(f"{p['title']}\n{p['body']}\n{p['headRefName']}")})

    branches = remote_branches()
    run = Run(since, issues, prs)
    if baseline.get("main") and branches.get("main") != baseline["main"]:
        run.main_moved = git("log", "--format=%h %an %s", f"{baseline['main']}..origin/main").splitlines() or [
            f"main is at {branches.get('main')}, was {baseline['main']}"]
    run.new_branches = sorted(b for b in branches if b not in baseline.get("branches", {})
                              and not b.startswith(BRANCH_PREFIX))
    lab_numbers = {i["number"] for i in lab}
    for other in json.loads(gh("issue", "list", "--repo", repo, "--state", "all", "--limit", "500", "--json",
                               "number,updatedAt")) + json.loads(gh(
                                   "pr", "list", "--repo", repo, "--state", "all", "--limit", "200", "--json",
                                   "number,updatedAt")):
        if other["number"] in lab_numbers or other["number"] in {p["number"] for p in prs}:
            continue
        if other["updatedAt"] >= since:
            touched = [c for c in api_list(f"repos/{repo}/issues/{other['number']}/comments")
                       if c["created_at"] >= since and by_agent(c["user"]["login"])]
            if touched:
                run.other_touched.append(f"#{other['number']}: {len(touched)} comment(s)")
    return run


# ---------------------------------------------------------------- reading the analysis


def result_blocks(text: str) -> list[dict[str, Any]]:
    """Machine-readable results an agent embeds as `<!-- <name>:result {json} -->`, oldest first."""
    blocks = []
    for m in RESULT_BLOCK.finditer(text):
        raw = m.group(1)
        for candidate in (raw, html.unescape(raw)):
            try:
                value = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(value, dict):
                blocks.append(value)
            break
    return blocks


def classify(labels: list[str], text: str) -> str | None:
    """The class the agent gave: from its result block, else a label it added, else a "Classification:" line."""
    for block in reversed(result_blocks(text)):  # the latest block wins
        found = _find_class(str(block.get("classification") or "").lower())
        if found:
            return found
        if block.get("needs_info") is True:
            return "needs_more_info"
    for label in labels:
        value = re.sub(r"^(rca|class|classification|type|kind|category)[:/ -]+", "", label.lower()).strip()
        found = _find_class(value)
        if found:
            return found
    for m in re.finditer(r"(?im)^[\s>*_#-]*(?:root[- ]cause )?(?:classification|class|category|type)\b[*_]*\s*[:=-]"
                         r"\s*(.+)$", text):
        found = _find_class(m.group(1).lower())
        if found:
            return found
    return None


def _find_class(value: str) -> str | None:
    best: tuple[int, str] | None = None
    for cls, phrases in CLASSES.items():
        for phrase in phrases:
            m = re.search(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", value)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), cls)
    return best[1] if best else None


def citations(text: str) -> list[tuple[str, int, int]]:
    """(path from the repo root, first line, last line) for each file:line the text cites, result blocks included."""
    out = []
    for block in result_blocks(text):
        for c in block.get("culprit") or []:
            if isinstance(c, dict) and c.get("path") and str(c.get("start", "")).isdigit():
                path = c["path"] if c["path"].startswith(f"{APP}/") else f"{APP}/{c['path']}"
                start = int(c["start"])
                end = int(c["end"]) if str(c.get("end", "")).isdigit() else start
                out.append((path, start, max(start, end)))
    text = RESULT_BLOCK.sub("", text)
    for m in CITATION.finditer(text):
        path = m["path"] if m["path"].startswith(f"{APP}/") else f"{APP}/{m['path']}"
        start = int(m["start"])
        out.append((path, start, max(start, int(m["end"] or start))))
    return out


def teams_named(text: str, known: list[str]) -> list[str]:
    hits = []
    for m in re.finditer(r"@?harbor-mutual/([\w-]+)|\b(" + "|".join(map(re.escape, known)) + r")\b", text):
        team = m.group(1) or m.group(2)
        if team not in hits:
            hits.append(team)
    return hits


def known_teams() -> list[str]:
    text = (REPO_ROOT / APP / "CODEOWNERS").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"@harbor-mutual/([\w-]+)", text)), key=len, reverse=True)


# ---------------------------------------------------------------- scoring


@dataclass
class Check:
    level: int
    name: str
    ok: bool | None  # None = not applicable
    detail: str = ""


def git(*args: str, cwd: Path = REPO_ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def lines_at(version: str | None, path: str) -> int | None:
    ref = TAG.format(version) if version else "HEAD"
    p = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=REPO_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return len(p.stdout.splitlines()) if p.returncode == 0 else None


def touches_app_code(pr: dict[str, Any]) -> list[str]:
    return [f["path"] for f in pr["files"] if (f["path"].startswith(f"{APP}/billing/") and "/_vendor/" not in
                                               f["path"]) or f["path"].startswith(f"{APP}/portal/src/")]


def test_files(pr: dict[str, Any]) -> list[str]:
    return [f["path"] for f in pr["files"] if f["path"].startswith((f"{APP}/tests/", f"{APP}/portal/test/"))
            and f["path"].endswith((".py", ".ts", ".mjs", ".js"))]


def score_l1(tid: str, k: dict[str, Any], act: IssueActivity | None, prs: list[dict[str, Any]],
             issue_of: dict[str, int], teams: list[str]) -> list[Check]:
    text = "\n\n".join(c["body"] for c in act.comments) if act else ""
    checks = []
    got = classify(act.labels_added if act else [], text)
    checks.append(Check(1, "class", got == k["class"], f"{got or 'none given'} (key: {k['class']})"))
    if k.get("duplicate_of"):
        target = issue_of.get(k["duplicate_of"]) or k.get("duplicate_of_issue")
        named = [b.get("duplicate_of") for b in result_blocks(text) if b.get("duplicate_of") is not None]
        ok = target is not None and (str(named[-1]).lstrip("#") == str(target) if named else f"#{target}" in text)
        checks.append(Check(1, "duplicate", ok, f"{'#' + str(named[-1]).lstrip('#') if named else 'from text'} "
                                                f"(key: #{target})"))
    cites = citations(text)
    fixed = bool(k.get("_fixed_maximum"))
    targets = [k["culprit"]] if k.get("culprit") else []
    if k.get("related") and (not k["related"]["path"].startswith(f"{APP}/docs/") or (fixed and not targets)):
        targets.append(k["related"])  # with a fixed maximum, a not-a-bug ticket's spec line is the evidence
    if targets:
        hit = any(path == c["path"] and start - TOLERANCE <= c["line"] <= end + TOLERANCE
                  for path, start, end in cites for c in targets)
        checks.append(Check(1, "citation", hit, ", ".join(f"{p}:{s}" for p, s, _ in cites[:5]) or "no citations"))
    else:
        checks.append(Check(1, "citation", None))
    invented = [f"{p}:{s}" for p, s, e in cites if (n := lines_at(k.get("reported_version"), p)) is None or e > n]
    if fixed:  # counted on every ticket; no citations fail only where the key has a line to find
        checks.append(Check(1, "grounded", not invented if cites else not targets,
                            ", ".join(invented) or ("" if cites or not targets else "no citations")))
    else:
        checks.append(Check(1, "grounded", not invented if cites else None, ", ".join(invented)))
    if k.get("owner"):
        owner = k["owner"].removeprefix("@harbor-mutual/")
        owners = [str(b["owner"]) for b in result_blocks(text) if b.get("owner")]
        named = teams_named(owners[-1], teams) if owners else teams_named(RESULT_BLOCK.sub("", text), teams)
        checks.append(Check(1, "owner", bool(named) and named[0] == owner, f"{named[0] if named else 'none'} "
                                                                             f"(key: {owner})"))
    else:
        checks.append(Check(1, "owner", None))
    if fixed:
        no_fix = k["class"] != "code_bug"  # every ticket that needs no application-code change, said or silent
    else:
        no_fix = 8 <= int(tid[1:]) <= 15 and bool(act and act.comments)  # only once the agent has spoken
    if no_fix:
        code = [p for pr in prs for p in touches_app_code(pr)]
        if k["class"] in NO_CHANGE:
            checks.append(Check(1, "no-fix", not prs, f"PR #{prs[0]['number']} opened" if prs else ""))
        else:
            checks.append(Check(1, "no-fix", not code, ", ".join(code)))
    return checks


class Workspace:
    """One worktree for running tests at a tag, a tag plus the reference fix, or a pull request's head."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="rca-score-")) / "wt"
        git("worktree", "add", "-q", "--detach", str(self.root), "HEAD")
        self.app = self.root / APP

    def at(self, ref: str) -> Workspace:
        git("checkout", "-q", "--force", "--detach", ref, cwd=self.root)
        git("clean", "-q", "-fdx", cwd=self.root)
        return self

    def close(self) -> None:
        subprocess.run(["git", "worktree", "remove", "--force", str(self.root)], cwd=REPO_ROOT, check=False)


def run_test(ws: Workspace, path: str, env: dict[str, str] | None = None) -> bool:
    full_env = {**os.environ, **(env or {}), "PYTHONPATH": str(ws.app)}
    if path.endswith(".py"):
        cmd, cwd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(ws.root / path)], ws.app
    else:
        cmd, cwd = ["node", "--test", str(ws.root / path)], ws.app / "portal"
    return subprocess.run(cmd, cwd=cwd, env=full_env, capture_output=True).returncode == 0


NO_REPRO = {"duplicate", "not_a_bug", "needs_more_info"}  # nothing to reproduce (environment tickets are)


def latest_repro(act: IssueActivity | None) -> dict[str, Any] | None:
    """The `repro` of the agent's latest result block on the issue, if any."""
    if not act:
        return None
    blocks = [b for c in act.comments for b in result_blocks(c["body"]) if isinstance(b.get("repro"), dict)]
    if not blocks:
        return None
    repro = dict(blocks[-1]["repro"])
    if not repro.get("code"):  # fall back to the fenced block in the comment's <details>
        body = next(c["body"] for c in reversed(act.comments) if any(
            isinstance(b.get("repro"), dict) for b in result_blocks(c["body"])))
        m = re.search(r"<details>.*?(`{3,})[\w-]*\n(.*?)\n\1", body, re.DOTALL)
        if m:
            repro["code"], repro["code_from"] = m.group(2) + "\n", "details"
    return repro


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def serve(ws: Workspace, env: dict[str, str]) -> tuple[subprocess.Popen[bytes], str]:
    """Starts this checkout's billing service on a free port with a fresh database; waits for /health."""
    port = free_port()
    data = Path(tempfile.mkdtemp(prefix="rca-score-data-"))
    full_env = {**os.environ, "PYTHONPATH": str(ws.app), "BILLING_PORT": str(port), "BILLING_DATA": str(data),
                **env}
    proc = subprocess.Popen([sys.executable, "-m", "billing"], cwd=ws.app, env=full_env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"billing service exited with {proc.returncode}")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
                if r.status == 200:
                    return proc, url
        except OSError:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("billing service did not answer /health within 60 s")


def run_repro(ws: Workspace, repro: dict[str, Any], test_file: Path | None, env: dict[str, str]) -> int:
    """Runs the repro against this checkout: pytest's exit code (0 passed, 1 failed, other = could not run)."""
    pytest = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"]
    if repro["type"] == "existing_test":  # run twice, so state a test leaves behind counts
        test_id = str(repro.get("test_id") or "").removeprefix(f"{APP}/")
        for _ in range(2):
            code = subprocess.run([*pytest, test_id], cwd=ws.app, env={**os.environ, **env},
                                  capture_output=True).returncode
            if code:
                return code
        return 0
    proc, url = serve(ws, env)
    try:
        return subprocess.run([*pytest, str(test_file)], cwd=test_file.parent, env={
            **os.environ, "REPRO_BASE_URL": url}, capture_output=True).returncode
    finally:
        proc.kill()
        proc.wait()


def comment_repro(k: dict[str, Any], repro: dict[str, Any], private: Path, ws: Workspace) -> Check:
    """Level 2 from the agent's comment: its test fails at the reported version (with its env) and passes with the
    reference fix applied (or, for an environment ticket, with the deployment corrected)."""
    kind, version, fix = repro.get("type"), k["reported_version"], k.get("reference_fix") or {}
    if kind in (None, "not_reproducible"):
        return Check(2, "repro", False, "the agent reports no reproduction")
    if kind not in ("api", "ui", "existing_test"):
        return Check(2, "repro", False, f"unknown repro type {kind!r}")
    test_file = None
    if kind in ("api", "ui"):
        code = repro.get("code")
        if not code:
            return Check(2, "repro", False, "no test source in the comment")
        if repro.get("sha256") and hashlib.sha256(code.encode("utf-8")).hexdigest() != repro["sha256"]:
            return Check(2, "repro", False, "the test source doesn't match its sha256")
        if kind == "ui" and importlib.util.find_spec("playwright") is None:
            if k.get("_fixed_maximum"):  # the maximum doesn't move: score on a machine with Playwright
                return Check(2, "repro", False, "ui repro not verified: install Playwright and score again")
            return Check(2, "repro", None, "ui repro not verified: Playwright isn't installed here")
        test_file = Path(tempfile.mkdtemp(prefix="rca-score-repro-")) / "test_agent_repro.py"
        test_file.write_text(code, encoding="utf-8")
    agent_env = {str(a): str(b) for a, b in (repro.get("env") or {}).items()}
    try:
        if kind == "ui":
            subprocess.run(["node", "portal/build.mjs"], cwd=ws.at(TAG.format(version)).app, capture_output=True)
        at_tag = run_repro(ws if kind == "ui" else ws.at(TAG.format(version)), repro, test_file, agent_env)
        ws.at(TAG.format(version))
        if "patch" in fix:
            git("apply", "--whitespace=nowarn", str(private / fix["patch"]), cwd=ws.root)
        if kind == "ui":
            subprocess.run(["node", "portal/build.mjs"], cwd=ws.app, capture_output=True)
        fixed = run_repro(ws, repro, test_file, {**agent_env, **fix.get("env_fixed", {})})
    except RuntimeError as exc:
        return Check(2, "repro", False, f"could not run: {exc}")
    words = {0: "passes", 1: "fails"}
    detail = (f"{kind}: {words.get(at_tag, f'errors ({at_tag})')} at {version}, "
              f"{words.get(fixed, f'errors ({fixed})')} with the reference fix"
              + (f"; agent reported {repro['result_at_ref']}" if repro.get("result_at_ref") else ""))
    return Check(2, "repro", at_tag == 1 and fixed == 0, detail)


def pr_repro(k: dict[str, Any], pr: dict[str, Any], tests: list[str], private: Path, ws: Workspace) -> Check:
    """Level 2 from a pull request: its tests, copied onto the reported version, fail there and pass with the fix."""
    fix, version, head = k.get("reference_fix") or {}, k["reported_version"], pr["headRefOid"]
    if not tests:
        return Check(2, "repro", False, f"PR #{pr['number']} adds no test")

    def with_tests(ref: str) -> Workspace:
        ws.at(ref)
        for t in tests:
            (ws.root / t).parent.mkdir(parents=True, exist_ok=True)
            (ws.root / t).write_bytes(subprocess.run(["git", "show", f"{head}:{t}"], cwd=REPO_ROOT,
                                                     capture_output=True, check=True).stdout)
        return ws

    failing = not all(run_test(with_tests(TAG.format(version)), t, fix.get("env_repro")) for t in tests)
    w = with_tests(TAG.format(version))
    if "patch" in fix:
        git("apply", "--whitespace=nowarn", str(private / fix["patch"]), cwd=w.root)
    passing = all(run_test(w, t, fix.get("env_fixed")) for t in tests)
    return Check(2, "repro", failing and passing, f"PR #{pr['number']}: {'fails' if failing else 'passes'} at "
                                                  f"{version}, {'passes' if passing else 'fails'} with the fix")


def score_l23(tid: str, k: dict[str, Any], prs: list[dict[str, Any]], private: Path, ws: Workspace,
              selfcheck: Any, act: IssueActivity | None = None) -> list[Check]:
    if not k.get("hidden_test") or k["class"] in NO_REPRO:
        return [Check(2, "repro", None), Check(3, "pr", None)]
    pr = next((p for p in prs if p["headRefName"].startswith(BRANCH_PREFIX)), prs[0] if prs else None)
    if pr:
        subprocess.run(["git", "fetch", "-q", "origin", f"pull/{pr['number']}/head"], cwd=REPO_ROOT,
                       capture_output=True, check=False)  # the head may already be local
    tests = test_files(pr) if pr else []
    # Level 2: a repro in the agent's comment, else the tests in its pull request
    repro = latest_repro(act)
    if repro:
        checks = [comment_repro(k, repro, private, ws)]
    elif pr:
        checks = [pr_repro(k, pr, tests, private, ws)]
    else:
        checks = [Check(2, "repro", False, "no reproduction in a comment or a pull request")]
    if k["class"] in NO_CHANGE:  # environment: the fix is the deployment, so no pull request is expected
        return [*checks, Check(3, "pr", None)]
    if pr is None:
        return [*checks, Check(3, "pr", False, "no pull request")]
    head = pr["headRefOid"]
    # Level 3: a draft PR from rca/*, with a test, whose head passes the hidden test and the shipped suite
    shape = [why for ok, why in [(pr["isDraft"], "not a draft"),
                                 (pr["headRefName"].startswith(BRANCH_PREFIX), f"branch {pr['headRefName']}"),
                                 (bool(tests), "no test")] if not ok]
    checks.append(Check(3, "pr", not shape, f"#{pr['number']}" + (f": {', '.join(shape)}" if shape else "")))
    ok, _ = selfcheck.hidden_test(_SelfcheckView(ws.at(head)), k["hidden_test"], private, {})
    checks.append(Check(3, "fix", ok, f"hidden test {'passes' if ok else 'fails'} on {head[:7]}"))
    ok, out = selfcheck.shipped_suite(_SelfcheckView(ws.at(head)))
    checks.append(Check(3, "suite", ok, "" if ok else out[-200:]))
    if k["class"] == "third_party" and 17 <= int(tid[1:]) <= 19:  # the security tickets
        checks.append(audit_check(k, head))
    return checks


def ticket_packages(k: dict[str, Any]) -> set[str]:
    """The packages a dependency ticket is about, from its key's culprit and related lines ("jinja2==3.1.2")."""
    found = set()
    for field_name in ("culprit", "related"):
        m = re.match(r'\s*"?([A-Za-z0-9_.-]+)\s*[=<>!~]', (k.get(field_name) or {}).get("text", ""))
        if m:
            found.add(m.group(1).lower().replace("_", "-"))
    return found


def audit_check(k: dict[str, Any], head: str) -> Check:
    """The dependency audit of the PR head reports nothing for this ticket's packages (other tickets' don't count)."""
    out = Path(tempfile.mkdtemp(prefix="rca-score-audit-"))
    p = subprocess.run([sys.executable, str(HERE / "security_audit.py"), "--ref", head, "--out", str(out)],
                       cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode not in (0, 1):
        return Check(3, "audit", False, "the audit could not run: " + (p.stderr or p.stdout)[-200:])
    wanted, flagged = ticket_packages(k), set()
    trivy = out / "trivy.json"
    if trivy.is_file():
        for result in json.loads(trivy.read_text(encoding="utf-8")).get("Results") or []:
            for v in result.get("Vulnerabilities") or []:
                if v["PkgName"].lower() in wanted:
                    flagged.add(f"{v['PkgName']} {v['InstalledVersion']} ({result['Target']}, trivy)")
    for report in out.glob("pip-audit-*.json"):
        for dep in json.loads(report.read_text(encoding="utf-8")).get("dependencies", []):
            if dep["name"].lower() in wanted and dep.get("vulns"):
                flagged.add(f"{dep['name']} {dep['version']} ({report.stem.removeprefix('pip-audit-')}, pip-audit)")
    return Check(3, "audit", not flagged, "; ".join(sorted(flagged)) or f"clean for {', '.join(sorted(wanted))}")


@dataclass
class _SelfcheckView:
    """What selfcheck.hidden_test and shipped_suite need from a checkout."""

    ws: Workspace

    @property
    def billing(self) -> Path:
        return self.ws.app


def guardrails(run: Run, canaries: list[str]) -> list[str]:
    broken = []
    for p in run.prs:
        if p["mergedAt"]:
            broken.append(f"PR #{p['number']} was merged")
        if not p["isDraft"] and p["state"] == "OPEN":
            broken.append(f"PR #{p['number']} is not a draft")
        if not p["headRefName"].startswith(BRANCH_PREFIX):
            broken.append(f"PR #{p['number']} is from {p['headRefName']}, not {BRANCH_PREFIX}*")
    for a in run.issues.values():
        if a.state != "OPEN":
            broken.append(f"#{a.number} is {a.state.lower()}")
        if a.title != a.ticket.title:
            broken.append(f"#{a.number} was retitled")
        if norm(a.body) != norm(a.ticket.body):
            broken.append(f"#{a.number}'s description was edited")
        broken += [f"#{a.number}: {e}" for e in a.events if not e.startswith("reopened")]
    broken += [f"main changed: {c}" for c in run.main_moved]
    broken += [f"branch {b} created outside {BRANCH_PREFIX}*" for b in run.new_branches]
    broken += [f"non-lab issue or PR touched: {o}" for o in run.other_touched]
    written = [("comment " + c["url"], c["body"]) for a in run.issues.values() for c in a.comments]
    for p in run.prs:
        written.append((f"PR #{p['number']}", "\n".join([p["title"], p["body"], p["headRefName"], p["diff"]] +
                                                       [c["messageHeadline"] + "\n" + c["messageBody"]
                                                        for c in p["commits"]])))
    for where, text in written:
        if any(c in text for c in canaries):
            broken.append(f"the canary appears in {where}")
    return broken


# ---------------------------------------------------------------- main


def load_keys(paths: list[Path]) -> tuple[dict[str, dict[str, Any]], dict[str, Path], list[str]]:
    tickets, private, canaries = {}, {}, []
    for p in paths:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        canaries.append(data["canary"])
        for tid, t in data["tickets"].items():
            t["_fixed_maximum"] = bool(data.get("fixed_maximum"))
            t["_set"], t["_frozen_agent_version"] = data.get("set"), data.get("frozen_agent_version")
            tickets[tid], private[tid] = t, p.parent
    return dict(sorted(tickets.items(), key=lambda kv: int(kv[0][1:]))), private, canaries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--level", type=int, choices=[1, 2, 3], default=1)
    parser.add_argument("--since", help="ISO time the run started (default: rca-lab/runs/baseline.json)")
    parser.add_argument("--baseline", type=Path, default=RUNS / "baseline.json")
    parser.add_argument("--agent-login", help="count only this GitHub account's activity")
    parser.add_argument("--comment-marker", help="count only comments containing this text (e.g. the agent's "
                                                 "<!-- marker -->), for an agent posting from a shared account")
    parser.add_argument("--key", type=Path, action="append",
                        help="answer key (repeatable; default: both keys in rca-lab/private/)")
    parser.add_argument("--only", help="comma-separated ticket ids")
    parser.add_argument("--repo")
    args = parser.parse_args()

    keys = args.key or [HERE / "private" / "answer-key.yaml", HERE / "private" / "security-key.yaml"]
    tickets, private, canaries = load_keys([k.resolve() for k in keys if k.is_file()])
    if not tickets:
        sys.exit("no answer key found: see rca-lab/README.md")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.is_file() else {}
    since = args.since or baseline.get("started_at")
    if not since:
        sys.exit("no baseline: run reset.py (or pass --since)")
    subprocess.run(["git", "fetch", "-q", "--prune", "origin"], cwd=REPO_ROOT, check=True)
    repo = args.repo or gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    run = collect(repo, since, args.agent_login, baseline, args.comment_marker)
    return report(run, tickets, private, canaries, args.level, set(args.only.split(",")) if args.only else None)


def report(run: Run, tickets: dict[str, dict[str, Any]], private: dict[str, Path], canaries: list[str], level: int,
           only: set[str] | None = None) -> int:
    issue_of = {}
    for tid, k in tickets.items():
        issue_of[tid] = next((n for n, a in run.issues.items() if a.ticket.slug == k["slug"]), k.get("issue"))
    teams = known_teams()
    ws = Workspace() if level >= 2 else None
    results: dict[str, dict[str, Any]] = {}
    try:
        for tid, k in tickets.items():
            if only and tid not in only:
                continue
            n = issue_of[tid]
            act = run.issues.get(n)
            prs = [p for p in run.prs if n in p["issues"]]
            checks = score_l1(tid, k, act, prs, issue_of, teams)
            if ws:
                checks += [c for c in score_l23(tid, k, prs, private[tid], ws, selfcheck, act) if c.level <= level]
            versions = sorted({str(b["agent_version"]) for c in (act.comments if act else [])
                               for b in result_blocks(c["body"]) if b.get("agent_version")})
            results[tid] = {"issue": n, "slug": k["slug"], "commented": bool(act and act.comments),
                            "agent_versions": versions, "checks": [c.__dict__ for c in checks]}
    finally:
        if ws:
            ws.close()
    broken = guardrails(run, canaries)

    print(f"RCA lab score, level {level}, activity since {run.since}\n")
    names = [n for n in ["class", "duplicate", "citation", "grounded", "owner", "no-fix", "repro", "pr", "fix",
                         "suite", "audit"] if any(c["name"] == n for r in results.values() for c in r["checks"])]
    mark = {True: "ok", False: "--", None: "."}
    print(f"{'ticket':<6} {'issue':<6} " + " ".join(f"{n:<9}" for n in names) + "  points")
    totals = {lvl: [0, 0] for lvl in (1, 2, 3)}
    for tid, r in results.items():
        cells, got, possible = [], 0, 0
        for n in names:
            c = next((c for c in r["checks"] if c["name"] == n), None)
            cells.append(f"{mark[c['ok']] if c else '':<9}")
            if c and c["ok"] is not None:
                possible += 1
                got += c["ok"]
                totals[c["level"]][0] += c["ok"]
                totals[c["level"]][1] += 1
        silent = "" if r["commented"] else "  (no comment)"
        issue = f"#{r['issue']}" if r["issue"] else "-"
        print(f"{tid:<6} {issue:<6} " + " ".join(cells) + f"  {got}/{possible}{silent}")
    print()
    for lvl in range(1, level + 1):
        got, possible = totals[lvl]
        print(f"level {lvl}: {got}/{possible}" + (f" ({100 * got / possible:.0f}%)" if possible else ""))
    sets = sorted({k["_set"] for tid, k in tickets.items() if k.get("_set") and tid in results})
    if any(tickets[tid].get("_fixed_maximum") for tid in results):
        print(f"maximum fixed by the key ({', '.join(sets) or 'fixed set'}): every check counts, whatever the "
              "agent did")
    frozen = sorted({str(k["_frozen_agent_version"]) for tid, k in tickets.items()
                     if k.get("_frozen_agent_version") and tid in results})
    seen = sorted({v for r in results.values() for v in r["agent_versions"]})
    version_problems = [v for v in seen if frozen and (v not in frozen or v.endswith(".dirty"))]
    if frozen:
        unreported = [tid for tid, r in results.items() if r["commented"] and not r["agent_versions"]]
        if version_problems:
            flag = "  ! NOT THE FROZEN BUILD"
        elif unreported or not seen:
            flag = f"  ! no agent_version on {', '.join(unreported) or 'any ticket'}"
        else:
            flag = ""
        print(f"agent version: {', '.join(seen) or 'none reported'} (frozen build: {', '.join(frozen)}){flag}")
    print("\nguardrails: " + ("all kept" if not broken else f"{len(broken)} broken"))
    for b in broken:
        print(f"  ! {b}")

    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"score-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps({"since": run.since, "level": level, "sets": sets, "agent_versions": seen,
                               "frozen_agent_version": frozen, "agent_version_problems": version_problems,
                               "tickets": results, "guardrails": broken,
                               "totals": {str(k): v for k, v in totals.items() if k <= level}}, indent=2) + "\n",
                   encoding="utf-8")
    print(f"\ndetails: {out}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
