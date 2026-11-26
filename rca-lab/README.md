# RCA lab

Tickets for testing an RCA (root-cause analysis) agent. The target application is
[`billing-service`](../billing-service). The design, with the ticket catalogue, the reproduction surfaces and the
scoring, is in [`docs/rca-agent-test-application.md`](../docs/rca-agent-test-application.md).

For each ticket, the agent works at one of three levels. It analyses and advises, also reproduces the problem with a
test, or also proposes a fix as a draft PR. Every ticket's right answer is known in advance. A scorer compares what
the agent did against that answer key.

## Layout

| Path | What | Committed |
|---|---|---|
| `tickets/*.md` | The ticket texts, filed as GitHub issues labelled `rca-lab`. The front matter holds `order`, `title` and `labels`; the body is the issue text | yes |
| `selfcheck.py` | Proves the answer key against the billing service's tagged history | yes |
| `security_audit.py` | Dependency security audit of any revision with the `sec-utils` scanners, as one HTML report | yes |
| `private/` | The answer keys (`answer-key.yaml`; `security-key.yaml` for the security tickets), the hidden tests and the reference fixes | **never** (git-ignored) |
| `audit-reports/` | Output of `security_audit.py` | no (git-ignored) |

The tickets are written as members and staff would write them, so they don't name a cause. Some are vague on
purpose.

## Keeping the answers out of reach

This repository is public and the agent checks it out. So:

- The answer key and hidden tests live only in `rca-lab/private/`, or wherever `RCA_LAB_KEY_PATH` points. Never commit
  them. Never write a cause, culprit line or fix into a ticket, a commit message, a code comment or any committed doc.
- `main`'s history is append-only. The planted defects are real commits, found with `git log` and `git blame`.
  Rewriting history would break the answer key.
- The key carries a canary string. A run fails if the canary appears in anything the agent writes.

## Self-check

Run it with the billing service's virtualenv, which has pytest, ruff and the app's dependencies:

```bat
billing-service\.venv\Scripts\python rca-lab\selfcheck.py                 :: every ticket in the key
billing-service\.venv\Scripts\python rca-lab\selfcheck.py --only T01,T02  :: some tickets
billing-service\.venv\Scripts\python rca-lab\selfcheck.py --no-suite      :: skip the shipped suite after each fix
```

The check works in a temporary git worktree, moving it between the `billing-service-v<version>` tags. For each
ticket it checks that:

1. the ticket file exists and states the version the reporter saw;
2. the culprit line in the key matches that line's text at that version's tag;
3. the hidden test fails at that version (in the reporter's environment, where the key gives one);
4. with the reference fix applied, the hidden test passes and the shipped suite stays green (ruff, pytest with the
   coverage gate, the portal's node tests);
5. for a regression, the hidden test passes at the version that worked.

`--key PATH` picks another key. The hidden tests and fixes are resolved next to the key.

## Security tickets and the audit

The tickets labelled `security` are findings from an external assessment of billing service 1.3.0. For these, a
fix is accepted when the hidden tests pass, the shipped suite stays green, and the dependency audit of the fix is
clean:

```bat
python rca-lab\security_audit.py --ref billing-service-v1.3.0   :: the reported version: findings, exit 1
python rca-lab\security_audit.py --ref rca/<fix-branch>         :: the agent's fix: "clean", exit 0
python rca-lab\security_audit.py                                :: the working tree
python rca-lab\security_audit.py --offline                      :: trivy only, no network
```

The audit uses the scanners in the `sec-utils` bundle (`--sec-utils`, else `SEC_UTILS`, else a `sec-utils` folder
next to this repository). trivy runs offline with the bundle's vulnerability database. pip-audit needs PyPI and OSV.
`srk render consolidated` writes one HTML report. Results go to `rca-lab/audit-reports/<ref>/`. Exit code 0 means
clean, 1 means vulnerabilities were found, and 2 means a scanner could not run.

To check the security answer key, run the self-check with `--key` pointing at `security-key.yaml`. Use an absolute
path, because the patches are resolved from inside a worktree.

## Not built yet

These come next in the design's build order: `file_tickets.py` (files the tickets as GitHub issues, dry run first),
`reset.py` (touches only `rca-lab` issues and `rca/*` branches) and `score.py`.
