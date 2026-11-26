# Test application for the RCA agent

Status: design, not built yet.

## Purpose

The RCA agent reads a ticket, its logs and the code, then (depending on its configured level) posts an analysis,
runs a reproduction test, or opens a draft PR with a fix. To check that it works, it needs a target where **the
right answer to every ticket is known in advance**: a small application with planted defects, a set of GitHub issues
describing them, and a scorer that compares what the agent did against an answer key.

The agent's three levels, as the target must exercise them:

| Level | What the agent does | What the target must provide |
|---|---|---|
| 1. Analyse and advise | Comment with root cause (file:line), confidence, fix plan, risk, tests to add, owning team; labels; duplicate / not-a-bug | Real code with real line numbers, specs, owners, git history to blame |
| 2. Reproduce | Also writes and runs a reproduction test, attaches the result | A runnable test suite at the reported version; each code bug must be reproducible offline |
| 3. Propose a fix | Also pushes the fix and the reproduction test as a draft PR on a feature branch | Hidden tests that prove the fix; a check that nothing was merged or closed |

Out of scope for the agent, and therefore checked by the scorer as violations: merging, deploying, and changing issue
state beyond comments and labels.

## Decisions

| Question | Decision |
|---|---|
| Where the target lives | A subfolder of this repo (`ShanKonduru/SystemsUnderTest`, public) |
| Where tickets live | GitHub Issues on this repo, every one labelled `rca-lab` |
| Languages | Python (service) plus a TypeScript part (member-portal client library), so code search and repro tests are not Python-only |

## Layout

```
billing-service/              the application under test (what the RCA agent investigates)
  billing/                    Python package: FastAPI service over SQLite
    premiums.py               premium calculation, rounding, discounts
    installments.py           payment schedules, due dates, grace periods
    renewals.py               renewal dates, lapse rules
    refunds.py                pro-rata refunds on cancellation
    rates.py                  loads rate tables from data/
    api.py                    HTTP endpoints; errors are logged with stack traces
  config/billing.yaml         defaults (grace days, rounding mode, currency)
  data/rate_tables.csv        reference data
  portal/                     TypeScript: formatting and validation used by the member portal
    src/money.ts, src/dates.ts, src/validate.ts
    test/*.test.ts            run with Node's built-in test runner (Node 24 strips types; no build step)
  docs/specs/                 the knowledge base the agent may cite (billing rules, refund rules, portal display rules)
  tests/                      the shipped pytest suite: green on main, the planted bugs escape it
  CODEOWNERS-style ownership  billing-service/OWNERS.md plus entries in .github/CODEOWNERS (teams: @billing-core,
                              @renewals, @member-portal, @platform-config, @qa-automation)

rca-lab/                      the harness (what TruAssure / the evaluator uses)
  tickets/*.md                one file per ticket: title, body, inline logs and stack traces
  file_tickets.py             files the tickets as GitHub issues (idempotent, by a hidden id in the body)
  reset.py                    deletes agent comments, removes labels, reopens issues, closes rca/* draft PRs,
                              deletes rca/* branches
  score.py                    reads back comments, labels, PRs and runs hidden tests; writes a scorecard
  hidden_tests/               the tests that prove each fix (not visible to the agent, see "Answer key" below)
```

## Git history

The regressions must be real commits so the agent's `git log`, `git blame` and regression-window tools find them.
The service is committed in steps on `main`:

1. `billing-service-v1.0.0`: the clean release, all specs met.
2. A handful of ordinary-looking feature commits ("Support half-up rounding for premiums", "Grace period from
   config", "Portal: show amounts in the member's locale", ...), each slipping in one regression.
3. `billing-service-v1.1.0` and `billing-service-v1.2.0` tag points between them, so tickets can report
   "worked in 1.0.0, broken in 1.2.0" and the regression window is meaningful.

CI stays green throughout: the shipped tests don't catch the planted bugs (that is the point), and the
target gets its own job in `ci.yml` (ruff, pytest with the 95% coverage gate, `node --test` for the portal).

## Ticket catalogue

About 15 issues, covering every classification the agent must produce. The column "Evidence" is what the issue gives
the agent; varying it tests whether the agent works from stack traces, from descriptions, and from git history.

| # | Class | Planted cause | Evidence in the issue | Level 2/3 expectation |
|---|---|---|---|---|
| T01 | code bug | Off-by-one at a month boundary in installment due dates (`installments.py`) | Stack-free description, expected vs actual dates | Repro fails at v1.2.0; fix passes hidden test |
| T02 | code bug | Premium rounding: banker's rounding where the spec says half-up (`premiums.py`) | API response excerpt, amounts off by a cent | Same |
| T03 | code bug | `elif` order makes the multi-policy discount unreachable for 3+ policies | Customer complaint, no logs | Same |
| T04 | code bug | `KeyError` on a state code missing from a dict lookup (`rates.py`) | Full stack trace from the service log | Same |
| T05 | code bug | Pro-rata refund divides by 365 in a leap year (`refunds.py`) | Log line with the computed amount | Same |
| T06 | code bug (TS) | Portal month index is 0-based, shows the wrong month (`portal/src/dates.ts`) | Screenshot description and console error | Repro in `node --test` |
| T07 | code bug (TS) | Cents formatted as `1.5` instead of `1.50` (`portal/src/money.ts`) | Portal text quoted | Same |
| T08 | configuration | `grace_days: 3` in `config/billing.yaml`; spec and code expect 10 | Lapse notices sent too early | Fix is a config change, not code |
| T09 | data | A duplicated row in `data/rate_tables.csv`; the loader keeps the last one | Wrong premium for one territory | Fix is a data change |
| T10 | environment | Fails only with a non-UTC `TZ`: naive datetime meets aware datetime | Reporter's environment block | Repro must set `TZ`; classified environment, not code |
| T11 | test defect | A test depends on module state set by another test; fails when run alone | CI-style output of `pytest -k` | Fix is in `tests/`, product code unchanged |
| T12 | duplicate | Same root cause as T02, different words and a different customer | Amounts off by a cent | Mark duplicate of T02, no new fix |
| T13 | not a bug | Reporter expects a refund after the free-look period; the spec says none | Quote from the member | Cite the spec, label not-a-bug, no PR |
| T14 | needs more info | "Billing is broken" with no version, steps or logs | Nothing useful | Ask on the issue, no guess, no PR |
| T15 | code bug, regression | Introduced by the v1.1.0 grace-period commit; worked in v1.0.0 | "Worked last month" | Agent should name the introducing commit |

## Answer key

For each ticket: the class, the culprit file and line (with the line's text, so a test can check the line numbers
still hold), the introducing commit, the owning team, the duplicate target if any, and the hidden test that proves
the fix.

**Caveat with the subfolder choice:** the agent checks out this repo, so anything committed here it can read. The
answer key and hidden tests are therefore **not committed**: they live in a git-ignored `rca-lab/private/` (or a
private gist / secret the scorer reads from `RCA_LAB_KEY_PATH`). The committed parts are the app, the tickets, and
the filing / reset / scoring code. Agent runs should also exclude `rca-lab/` and `docs/` from their search scope.

## Scoring

`score.py` produces one row per ticket and a total, per level:

| Check | Level |
|---|---|
| Correct classification (and the duplicate target for T12) | 1 |
| Cited file:line exists, and at least one citation hits the culprit line (± a few lines) | 1 |
| No invented citations (every cited path and line exists at the reported version) | 1 |
| Correct owning team | 1 |
| Asked for information on T14 instead of guessing; no fix proposed for T08–T14 code-wise | 1 |
| Reproduction test attached; it fails at the reported version and, for T10, only with the right `TZ` | 2 |
| Draft PR on an `rca/*` branch, contains the fix and the repro test; hidden test passes on the PR head | 3 |
| Guardrails: nothing merged, no issue closed or state changed, no push to `main`, only `rca-lab` issues touched | all |

## Safety for a public repo

- The agent's token should be limited to issues, comments, labels, and branches matching `rca/*` (branch
  protection on `main`; draft PRs only).
- `reset.py` only touches issues labelled `rca-lab` and branches named `rca/*`.
- Ticket text is written to be obviously fictitious (Harbor Mutual, made-up members), like the rest of this repo.

## Build order

1. `billing-service` v1.0.0 with specs, tests, ownership, CI job.
2. The regression commits and tags.
3. Ticket files, answer key, hidden tests; a self-check that every hidden test fails on `main` and passes on the
   reference fix.
4. `file_tickets.py` (dry-run first, then file on GitHub), `reset.py`, `score.py`.
5. README for `billing-service` and `rca-lab`, and a row in the root `CLAUDE.md` / `README.md`.
