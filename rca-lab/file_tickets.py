"""Files the RCA lab tickets as GitHub issues. Dry run unless --file.

    python rca-lab\\file_tickets.py                     :: dry run: what would be filed, what already is
    python rca-lab\\file_tickets.py --file              :: file every ticket not filed yet, in `order`
    python rca-lab\\file_tickets.py --file --only billing-broken,pa-home-rate

Each `tickets/<slug>.md` becomes one issue: the front matter gives the title and labels, the text up to the first
`<!-- rca-lab:comment -->` is the body, and each later block starting with that marker is posted as a comment, in
order. A ticket whose title is already an issue labelled `rca-lab` (open or closed) is skipped, so a run that stops
part-way can be repeated. Missing labels are created. Needs the GitHub CLI (`gh`), signed in with access to the repo.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

TICKETS = Path(__file__).resolve().parent / "tickets"
COMMENT = "<!-- rca-lab:comment -->"
LAB_LABEL = "rca-lab"


@dataclass(frozen=True)
class Ticket:
    slug: str
    order: int
    title: str
    labels: list[str]
    body: str
    comments: list[str]


def parse(path: Path) -> Ticket:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: no front matter")
    head, _, rest = text[4:].partition("\n---\n")
    meta: dict[str, str] = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip()
    missing = {"order", "title", "labels"} - meta.keys()
    if missing:
        raise ValueError(f"{path.name}: front matter lacks {', '.join(sorted(missing))}")
    title = meta["title"]
    if len(title) >= 2 and title[0] == title[-1] == '"':
        title = json.loads(title)
    labels = [label.strip() for label in meta["labels"].strip("[]").split(",") if label.strip()]
    if LAB_LABEL not in labels:
        raise ValueError(f"{path.name}: labels must include {LAB_LABEL}")
    body, *comments = rest.split(COMMENT)
    return Ticket(path.stem, int(meta["order"]), title, labels, body.strip(), [c.strip() for c in comments if c.strip()])


def load(only: set[str] | None) -> list[Ticket]:
    tickets = sorted((parse(p) for p in TICKETS.glob("*.md")), key=lambda t: (t.order, t.slug))
    if only:
        unknown = only - {t.slug for t in tickets}
        if unknown:
            sys.exit(f"no ticket file for {', '.join(sorted(unknown))}")
        tickets = [t for t in tickets if t.slug in only]
    orders = [t.order for t in tickets]
    if len(orders) != len(set(orders)):
        sys.exit("two tickets share an `order`")
    return tickets


def gh(*args: str) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def body_file(text: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(text + "\n")
        return fh.name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", action="store_true", help="file the issues (default: dry run)")
    parser.add_argument("--only", help="comma-separated ticket slugs (file names without .md)")
    parser.add_argument("--repo", help="OWNER/NAME (default: the repo gh resolves from this checkout)")
    args = parser.parse_args()

    tickets = load(set(args.only.split(",")) if args.only else None)
    repo = args.repo or gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    existing = {i["title"]: i["number"] for i in json.loads(gh(
        "issue", "list", "--repo", repo, "--label", LAB_LABEL, "--state", "all", "--limit", "1000",
        "--json", "number,title"))} if LAB_LABEL in labels_on(repo) else {}
    todo = [t for t in tickets if t.title not in existing]

    print(f"{repo}: {len(tickets)} ticket(s), {len(tickets) - len(todo)} already filed, {len(todo)} to file"
          + ("" if args.file else " (dry run)"))
    for t in tickets:
        status = f"filed #{existing[t.title]}" if t.title in existing else ("filing" if args.file else "would file")
        extra = f", {len(t.comments)} comment(s)" if t.comments else ""
        print(f"  {t.order:>3}  {t.slug:<42} [{', '.join(t.labels)}]{extra}  {status}")
    if not args.file or not todo:
        return 0

    have = labels_on(repo)
    for label in sorted({label for t in todo for label in t.labels} - have):
        gh("label", "create", label, "--repo", repo)
        print(f"created label {label}")

    for t in todo:
        try:
            label_args = [a for label in t.labels for a in ("--label", label)]
            url = gh("issue", "create", "--repo", repo, "--title", t.title, "--body-file", body_file(t.body),
                     *label_args)
            number = url.rstrip("/").rsplit("/", 1)[-1]
            for comment in t.comments:
                gh("issue", "comment", number, "--repo", repo, "--body-file", body_file(comment))
        except RuntimeError as exc:
            print(f"stopped at {t.slug}: {exc}\nrun again to file the rest", file=sys.stderr)
            return 1
        print(f"  #{number}  {t.slug}" + (f" (+{len(t.comments)} comment(s))" if t.comments else ""), flush=True)
        time.sleep(1)  # stay clear of GitHub's secondary rate limits
    return 0


def labels_on(repo: str) -> set[str]:
    return set(gh("label", "list", "--repo", repo, "--limit", "500", "--json", "name", "--jq", ".[].name").split())


if __name__ == "__main__":
    sys.exit(main())
