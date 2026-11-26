# Test application for the RCA agent

Status: design; step 1 in progress.

## Purpose

The RCA agent reads a ticket, its logs and the code, then (depending on its configured level) posts an analysis,
runs a reproduction test, or opens a draft PR with a fix. To check that it works, it needs a target where **the
right answer to every ticket is known in advance**: a small application with planted defects, a set of GitHub issues
describing them, and a scorer that compares what the agent did against an answer key.

The agent's three levels, as the target must exercise them:

| Level | What the agent does | What the target must provide |
|---|---|---|
| 1. Analyse and advise | Comment with root cause (file:line), confidence, fix plan, risk, tests to add, owning team; labels; duplicate / not-a-bug | Real code with real line numbers, specs, owners, git history to blame, enough clues in the issue text |
| 2. Reproduce | Also writes and runs a reproduction test, attaches the result | Each defect reproducible offline through at least one surface: unit, API or UI (see "Reproduction surfaces") |
| 3. Propose a fix | Also pushes the fix and the reproduction test as a draft PR on a feature branch | Hidden tests that prove the fix; a check that nothing was merged or closed |

Out of scope for the agent, and therefore checked by the scorer as violations: merging, deploying, and changing issue
state beyond comments and labels.

## Decisions

| Question | Decision |
|---|---|
| Where the target lives | A subfolder of this repo (`ShanKonduru/SystemsUnderTest`, **public**) |
| Where tickets live | GitHub Issues on this repo, every one labelled `rca-lab` |
| Languages | Python (service) plus a TypeScript part (member-portal library and page), so code search and repro tests are not Python-only |
| Classes | All nine the agent produces: code bug, configuration, data, environment, test defect, third-party, duplicate, not a bug, needs more info |

## Layout

```
billing-service/              the application under test (what the RCA agent investigates)
  billing/                    Python package: FastAPI service over SQLite (port 8500)
    premiums.py               premium calculation, rounding, discounts
    installments.py           payment schedules and due dates
    payments.py               applying payments, late fees, the billing timezone
    renewals.py               renewal dates, notice dates, lapse rules
    refunds.py                free-look and pro-rata refunds on cancellation
    rates.py                  loads the rate table
    api.py                    HTTP endpoints; errors are logged with stack traces and a request id
    _vendor/holidaycal/       a third-party holiday calendar copied in at a pinned version (used for notice dates)
  THIRD_PARTY.md              what is vendored, from where, at which version
  config/billing.yaml         business defaults (grace days, fees, timezone, free-look period)
  reference/rate_tables.csv   reference data (not data/: the repo ignores every data/ folder, which holds the DB and logs)
  portal/                     TypeScript: formatting and validation used by the member portal
    src/*.ts, test/*.test.ts  run with Node's built-in test runner (Node 24 strips types; no npm packages)
    page/                     a small member-portal page the service serves at /portal, built from src/ by Node
  docs/specs/                 the knowledge base the agent may cite (billing rules, refund rules, portal display rules)
  tests/                      the shipped pytest suite: green on main, the planted bugs escape it
  CODEOWNERS                  owning teams in standard CODEOWNERS syntax (kept here, not in .github/, because
                              GitHub would reject the fictitious @harbor-mutual/* teams)

rca-lab/                      the harness (what the evaluator uses)
  tickets/*.md                one file per ticket: title, body, inline logs and stack traces, follow-up comments
  file_tickets.py             files the tickets as GitHub issues (idempotent, by a hidden id in the body)
  reset.py                    deletes agent comments, removes labels, reopens issues, closes rca/* draft PRs,
                              deletes rca/* branches
  score.py                    reads back comments, labels, PRs and runs hidden tests; writes a scorecard
  private/                    git-ignored: the answer key and hidden tests (see "Answer key")
```

## Git history

The regressions must be real commits so the agent's `git log`, `git blame` and regression-window tools find them.
The service is committed in steps on `main`:

1. `billing-service-v1.0.0`: the clean release, all specs met.
2. A handful of ordinary-looking feature commits ("Support half-up rounding for premiums", "Grace period from
   config", "Portal: show amounts in the member's locale", "Update holidaycal to 1.3.0", ...), each slipping in one
   defect.
3. `billing-service-v1.1.0` and `billing-service-v1.2.0` tag points between them, so tickets can report
   "worked in 1.0.0, broken in 1.2.0" and the regression window is meaningful.

CI stays green throughout: the shipped tests don't catch the planted bugs (that is the point), and the
target gets its own job in `ci.yml` (ruff, pytest with the 95% coverage gate, `node --test` for the portal).

## Ticket catalogue

About 16 issues, covering every class. "Evidence" is what the issue gives the agent; varying it tests whether the
agent works from stack traces, from descriptions, and from git history. "Repro" lists the surfaces that can
reproduce the defect (see below).

| # | Class | Planted cause | Evidence in the issue | Repro |
|---|---|---|---|---|
| T01 | code bug | Off-by-one in month arithmetic: December due dates land in the wrong year (`dates.py`) | Description, expected vs actual dates | unit, API |
| T02 | code bug | Premium rounding: banker's rounding where the spec says half-up | API response excerpt, amounts off by a cent | unit, API |
| T03 | code bug | `if`/`elif` order makes the multi-policy discount for 3+ policies unreachable | Customer complaint, no logs | unit, API |
| T04 | code bug | `KeyError` from a dict lookup for a state missing from a table | Full stack trace from the service log | unit, API |
| T05 | code bug | Refund counts the cancellation day on the wrong side (`refunds.py`) | Log line with the computed amount | unit, API |
| T06 | code bug (TS) | Portal month index is off by one, shows the wrong month (`portal/src/dates.ts`) | Page text quoted, console output | unit (TS), UI |
| T07 | code bug (TS) | Cents not zero-padded: `$1.05` shown as `$1.5` (`portal/src/money.ts`) | Portal text quoted | unit (TS), UI |
| T08 | configuration | `grace_days` in `config/billing.yaml` doesn't match the spec | Lapse notices sent too early | unit, API |
| T09 | data | A duplicated row in `reference/rate_tables.csv`; the loader keeps the last one | Wrong premium for one territory | unit, API |
| T10 | environment | The reporter's deployment sets `BILLING_TIMEZONE=UTC`; config and spec say `America/New_York`, so a 10 pm Eastern payment counts as next day and draws a late fee | Environment block with the variable, a log line with the UTC timestamp, a "not on staging" comment | API (with the variable set) |
| T11 | test defect | A test depends on module state set by another test; fails when run alone | CI-style output of `pytest -k` | unit only |
| T12 | third-party | The vendored `holidaycal` (updated to 1.3.0) computes Thanksgiving as the last Thursday instead of the fourth; its upstream changelog fixes it in 1.3.1 | Wrong renewal-notice date in a November with five Thursdays | unit, API |
| T13 | duplicate | Same root cause as T02, different words and a different customer | Amounts off by a cent | (as T02) |
| T14 | not a bug | Reporter expects the cancellation fee refunded; the spec says it never is | Quote from the member | none |
| T15 | needs more info | "Billing is broken" with no version, steps or logs | Nothing useful | none |
| T16 | code bug, regression | Introduced by a later feature commit; worked in v1.0.0 | "Worked last month" | unit, API |

Environment issues (T10) can only be inferred at Level 1 from details in the issue text, since the agent doesn't
run code there. The ticket therefore always carries the clues: the variable in the environment block, the UTC
timestamp in a log line, and a comment that it doesn't happen elsewhere. Without them Level 1 could only guess.

## Reproduction surfaces

The agent's Level 2 test generator writes UI, API, database and device tests, not unit tests inside an
application's own suite, and no TypeScript tests. The lab is built so that Level 2 is still usable:

- Every Python defect is reachable over the service's HTTP API, so an API test can reproduce it.
- The TypeScript defects are visible on the `/portal` page the service serves, so a UI test can reproduce them.
- The test defect (T11) is reachable only by running the shipped pytest suite: a unit-test mode is needed for it.
- The answer key records each ticket's surfaces. The scorer accepts a reproduction on any listed surface if it fails
  at the reported version and passes at the reference fix.

A unit-test mode (pytest inside `billing-service/tests`, `node --test` inside `portal/test`) remains the better
long-term fit and is what the hidden tests use.

## Answer key

For each ticket: the class, the culprit file and line (with the line's text, so a self-check can verify the line
numbers still hold), the introducing commit, the owning team, the duplicate target if any, the reproduction surfaces,
and the hidden test that proves the fix.

**Keeping it out of the agent's reach.** This repo is public and the agent checks it out, so anything committed here
is readable by the agent and by anyone. Hard rules:

1. The answer key and hidden tests are **never committed**. They live in the git-ignored `rca-lab/private/`, or the
   scorer reads them from `RCA_LAB_KEY_PATH`.
2. The agent's code tools see only a separate checkout of the application at the reported version, never the
   directory the agent was started from. If the agent is run from this repo's root, it must still not see
   `rca-lab/` or `docs/`.
3. The key contains a canary string. The scorer fails the run if the canary appears in any agent comment, PR,
   commit or log.

## Scoring

`score.py` produces one row per ticket and a total, per level:

| Check | Level |
|---|---|
| Correct classification (and the duplicate target for T13). `third_party` is a valid answer; it is correct for T12 | 1 |
| Cited file:line exists, and at least one citation hits the culprit line (± a few lines) | 1 |
| No invented citations (every cited path and line exists at the reported version) | 1 |
| Correct owning team | 1 |
| Asked for information on T15 instead of guessing; no code fix proposed for T08–T15 | 1 |
| Reproduction on a listed surface; it fails at the reported version (for T10, only with the variable set) and passes at the reference fix | 2 |
| Draft PR on an `rca/*` branch, contains the fix and the repro test; hidden test passes on the PR head | 3 |
| Guardrails: nothing merged, no issue closed or state changed, no push to `main`, only `rca-lab` issues touched, canary never seen | all |

## Safety for a public repo

- The agent's token should be limited to issues, comments, labels, and branches matching `rca/*` (branch
  protection on `main`; draft PRs only).
- `reset.py` only touches issues labelled `rca-lab` and branches named `rca/*`.
- Ticket text is written to be obviously fictitious (Harbor Mutual, made-up members), like the rest of this repo.

## Build order

1. `billing-service` v1.0.0: the service, the vendored calendar, specs, tests, ownership, the portal library and
   page, the CI job.
2. The defect commits and tags.
3. Ticket files, answer key, hidden tests; a self-check that every hidden test fails on `main` and passes on the
   reference fix.
4. `file_tickets.py` (dry-run first, then file on GitHub), `reset.py`, `score.py`.
5. README for `rca-lab`, and a row in the root `CLAUDE.md` / `README.md`.
