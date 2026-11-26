"""Puts the RCA lab back to its filed state before a run, and records the baseline score.py compares against.
Dry run unless --apply.

    python rca-lab\\reset.py              :: dry run: what would change
    python rca-lab\\reset.py --apply      :: reset, then write rca-lab/runs/baseline.json
    python rca-lab\\reset.py --baseline   :: only record the baseline (the lab is known to be clean)

It touches only issues labelled `rca-lab` and branches named `rca/*`:

- each rca-lab issue is reopened if closed, and its title, body and labels are restored from its ticket file;
- every comment that isn't one of the ticket's own `<!-- rca-lab:comment -->` blocks is deleted (a missing one is
  posted again);
- open pull requests from `rca/*` branches are closed, and the `rca/*` branches are deleted.

The baseline (time, `main`'s commit, the branches, each issue's state) lets score.py tell what a run changed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_tickets import LAB_LABEL, Ticket, body_file, gh, load

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS = REPO_ROOT / "rca-lab" / "runs"
BRANCH_PREFIX = "rca/"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r\n", "\n")).strip()


def api_list(path: str) -> list[dict[str, Any]]:
    out = gh("api", "--paginate", path, "--jq", ".[]")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def lab_issues(repo: str) -> list[dict[str, Any]]:
    return json.loads(gh("issue", "list", "--repo", repo, "--label", LAB_LABEL, "--state", "all", "--limit", "500",
                         "--json", "number,title,body,state,labels"))


def remote_branches() -> dict[str, str]:
    out = subprocess.run(["git", "ls-remote", "--heads", "origin"], cwd=REPO_ROOT, capture_output=True, text=True,
                         check=True).stdout
    return {ref.removeprefix("refs/heads/"): sha for sha, ref in (line.split("\t") for line in out.splitlines())}


def match_ticket(issue: dict[str, Any], tickets: list[Ticket]) -> Ticket | None:
    """The ticket an issue was filed from: by title, else (if the title was changed) by body."""
    by_title = {t.title: t for t in tickets}
    return by_title.get(issue["title"]) or next((t for t in tickets if norm(t.body) == norm(issue["body"])), None)


def plan(repo: str, tickets: list[Ticket]) -> list[tuple[str, list[str]]]:
    """(description, gh arguments) for every change the reset would make; empty arguments = report only."""
    actions: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for issue in lab_issues(repo):
        n = str(issue["number"])
        t = match_ticket(issue, tickets)
        if t is None:
            actions.append((f"#{n}: rca-lab issue matches no ticket file; left alone", []))
            continue
        seen.add(t.slug)
        if issue["state"] != "OPEN":
            actions.append((f"#{n}: reopen", ["issue", "reopen", n, "--repo", repo]))
        if issue["title"] != t.title:
            actions.append((f"#{n}: restore title", ["issue", "edit", n, "--repo", repo, "--title", t.title]))
        if norm(issue["body"]) != norm(t.body):
            actions.append((f"#{n}: restore body",
                            ["issue", "edit", n, "--repo", repo, "--body-file", body_file(t.body)]))
        have = {label["name"] for label in issue["labels"]}
        for label in sorted(have - set(t.labels)):
            actions.append((f"#{n}: remove label {label}",
                            ["issue", "edit", n, "--repo", repo, "--remove-label", label]))
        for label in sorted(set(t.labels) - have):
            actions.append((f"#{n}: add label {label}", ["issue", "edit", n, "--repo", repo, "--add-label", label]))
        keep = [norm(c) for c in t.comments]
        for c in api_list(f"repos/{repo}/issues/{n}/comments"):
            if norm(c["body"]) in keep:
                keep.remove(norm(c["body"]))  # one copy of each seeded comment stays
                continue
            actions.append((f"#{n}: delete comment {c['id']} by {c['user']['login']} ({c['created_at']})",
                            ["api", "-X", "DELETE", f"repos/{repo}/issues/comments/{c['id']}"]))
        for missing in keep:
            text = next(c for c in t.comments if norm(c) == missing)
            actions.append((f"#{n}: post the seeded comment again",
                            ["issue", "comment", n, "--repo", repo, "--body-file", body_file(text)]))
    for t in tickets:
        if t.slug not in seen:
            actions.append((f"{t.slug}: not filed (run file_tickets.py --file)", []))
    for pr in json.loads(gh("pr", "list", "--repo", repo, "--state", "open", "--limit", "500",
                            "--json", "number,headRefName")):
        if pr["headRefName"].startswith(BRANCH_PREFIX):
            actions.append((f"PR #{pr['number']} ({pr['headRefName']}): close",
                            ["pr", "close", str(pr["number"]), "--repo", repo]))
    for branch in sorted(remote_branches()):
        if branch.startswith(BRANCH_PREFIX):
            actions.append((f"branch {branch}: delete",
                            ["api", "-X", "DELETE", f"repos/{repo}/git/refs/heads/{branch}"]))
    return actions


def write_baseline(repo: str) -> Path:
    branches = remote_branches()
    baseline = {
        "repo": repo,
        "started_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "main": branches.get("main"),
        "branches": branches,
        "issues": {str(i["number"]): {"title": i["title"], "state": i["state"]} for i in lab_issues(repo)},
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / "baseline.json"
    path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="make the changes, then record the baseline")
    mode.add_argument("--baseline", action="store_true", help="only record the baseline")
    parser.add_argument("--repo", help="OWNER/NAME (default: the repo gh resolves from this checkout)")
    args = parser.parse_args()
    subprocess.run(["git", "fetch", "-q", "--prune", "origin"], cwd=REPO_ROOT, check=True)
    repo = args.repo or gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")

    if not args.baseline:
        actions = plan(repo, load(None))
        todo = [(what, cmd) for what, cmd in actions if cmd]
        for what, cmd in actions:
            print(("  " if args.apply or not cmd else "  would ") + what)
        if not todo:
            print("the lab is clean")
        if not args.apply:
            print(f"{len(todo)} change(s); dry run, nothing changed")
            return 0
        for what, cmd in todo:
            try:
                gh(*cmd)
            except RuntimeError as exc:
                print(f"stopped at '{what}': {exc}\nrun again to finish the reset", file=sys.stderr)
                return 1
    print(f"baseline: {write_baseline(repo)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
