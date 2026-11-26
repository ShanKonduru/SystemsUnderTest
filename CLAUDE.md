# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Demo "systems under test" (SUT) for the fictitious insurer **Harbor Mutual**, which the separate TruAssure project
tests **black-box** (it never imports this code): six independent Python apps over HTTP, one Windows desktop app driven
through UI Automation, plus one shared library. `rca-lab/` holds the tickets for the RCA agent, whose target is
`billing-service`:

| Dir | Package | What it is | Ports |
|---|---|---|---|
| `common/harbor_llm` | `harbor_llm` (src layout) | Shared LLM factory used by every app | — |
| `chat-assistant` | `insurance_copilot` | Customer chat: intent routing → plan → MCP tool loop, SQLite | 8000 |
| `claims-agent` | `claims_agent` | Event-driven back-office agent, no chat; agent + MCP tool server | 8100, 9100 |
| `expense-agent` | `expense_agent` | Event-driven expense agent, no chat, no host guards (plain model + tools); agent + MCP tool server | 8700, 9700 |
| `multi-agent-desk` | `desk` | Triage → billing → claims agents with signed identity propagation | 8300 (triage), 9300 (tools) |
| `automation-lab` | `automation_lab` | No-login web UI for test-script generation/execution/self-healing; **no LLM**, no `harbor_llm` dependency | 8400 |
| `policy-desk` | C# (`PolicyDesk`) + pytest suite | Windows WinForms thick client for desktop automation, script generation and self-healing; baseline suite in pywinauto; **no LLM, no image** | — |
| `billing-service` | `billing` + TypeScript `portal/` | Premiums, installments, payments, renewals, refunds (FastAPI, SQLite) and the member portal's billing page; the RCA agent's target, defects planted in **git history**; **no LLM**, no `harbor_llm` dependency | 8500 |

Because TruAssure tests these apps, **behaviour visible over HTTP, tool names/arguments, seed data, response
shapes and the planted defects are a contract**. CI publishes `ghcr.io/<owner>/harbor-{claims-agent,expense-agent,member-desk,chat-assistant,automation-lab,billing-service}`
tagged `sha-<commit>` and `latest` on every green push to `main`; TruAssure pins a `sha-` tag. `expense-agent` is also on PyPI as
`harbor-expense-agent` (wheel carries `harbor_llm`; console command `harbor-expense-agent`), published by
`publish-expense-agent.yml` on a tag `expense-agent-v<version>`: TruVelocity's `tv practice-agent` runs it without Docker.

## Commands

Each app is its own project; run commands from the app's directory. Python 3.12 (>=3.11 supported). Install the
shared factory first (editable, from the sibling path):

```bat
cd chat-assistant            :: or claims-agent / expense-agent / multi-agent-desk
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ..\common\harbor_llm[all] -r requirements.txt pytest pytest-asyncio pytest-cov httpx ruff
```

| Project | Lint (as CI runs it) | Tests (95% branch-coverage gate) |
|---|---|---|
| `common/harbor_llm` | `ruff check src tests` and `mypy` (strict) | `python -m pytest -q --cov` (install with `-e ".[all,dev]"`) |
| `chat-assistant` | `ruff check insurance_copilot tests` | `python -m pytest -q --cov --cov-fail-under=95` |
| `claims-agent` | `ruff check claims_agent tests` | `python -m pytest -q --cov` |
| `expense-agent` | `ruff check expense_agent tests` | `python -m pytest -q --cov` |
| `multi-agent-desk` | `ruff check desk tests` | `python -m pytest -q --cov` |
| `automation-lab` | `ruff check automation_lab tests` | `python -m pytest -q --cov` (install: `-r requirements.txt pytest pytest-cov httpx ruff`, no `harbor_llm`) |
| `policy-desk` | `app\build.bat` (csc, `/warnaserror+`); `ruff check screens tests tools` for the suite | `app\bin\policydesk-cli.exe --self-test`. Local only (needs a desktop): `python -m pytest` (baseline suite; install `-r requirements-test.txt`), `python tools\defect_matrix.py`, `tools\smoke.bat` |
| `billing-service` | `ruff check billing tests` (`billing/_vendor` excluded) | `python -m pytest -q --cov`; portal: `cd portal && npm test` (`node --test` on the `.ts` sources, Node 24+, no packages; `node build.mjs` builds `dist/` for `/portal`). Install: `-r requirements.txt pytest pytest-cov httpx ruff`, no `harbor_llm` |
| `rca-lab` | — | `billing-service\.venv\Scripts\python rca-lab\selfcheck.py` (needs the private answer key; `--only T01,T02`, `--no-suite`; `--key <absolute path>\security-key.yaml` for the security tickets). `python rca-lab\file_tickets.py [--file]` files the tickets as GitHub issues (dry run by default, skips filed ones). `python rca-lab\security_audit.py [--ref <ref>]`: dependency audit with the sibling `sec-utils` bundle (trivy offline, pip-audit, `srk` HTML report); exit 0 = clean |

Single test: `python -m pytest tests/test_engine.py::test_name -q`. All tests are offline (scripted LLM, no keys);
pytest uses `asyncio_mode = "auto"`, so async tests need no decorator.

Docker (from the **repo root**; build context is the root so `common/harbor_llm` can be copied in):

```bat
docker compose -f claims-agent\docker-compose.yml up -d --build
docker compose -f claims-agent\docker-compose.yml -f claims-agent\docker-compose.observed.yml up -d --build   :: with TruAssure's recording proxy
```

## Architecture notes

**LLM factory (`harbor_llm`).** `make_llm(override_var=...)` picks a provider from `<APP>_LLM_PROVIDER`
(`CHAT_`/`CLAIMS_`/`EXPENSE_`/`DESK_`) or `DEFAULT_LLM_PROVIDER`, read from the root `.env` (copy `.env.example`). Every
provider is normalized to one Anthropic-style interface: `await llm.create(messages, system, tools, ...)` returning
`LLMResponse` of `TextBlock`/`ToolUseBlock`, and `await llm.structured(system, user, schema=PydanticModel)`. Apps
code only against this interface. The `SCRIPTED` provider is how everything runs offline:
`LLM_SCRIPT=pkg.module:factory` loads an app-supplied rule-based policy (`claims_agent.policy:factory`,
`expense_agent.policy:factory`, `desk.policy:factory`), or `LLM_SCRIPT_FILE` replays a JSONL of recorded responses.

**Common app shape.** Each app = an agent loop + an MCP tool server over a SQLite system of record, with:
- a fixed "today" (`*_TODAY`, default `2026-11-03`) so outputs are reproducible;
- token-protected `/sandbox/status|reset` (and `/sandbox/faults` in the desk) test-control endpoints that refuse
  everything when the token env var is empty;
- the principal passed in a header by the host, never chosen by the model (`X-Customer-Id` for chat,
  `X-Principal` for claims, expense and desk); the tool server enforces that principal's permissions;
- a returned "account" of the episode (route, steps/tool calls, output, done) that TruAssure compares against
  independent records (proxy logs, OTel spans, DB diffs);
- `docker-compose.observed.yml` that splits tools into their own service and puts TruAssure's proxy image
  (`TRUASSURE_PROXY_IMAGE`) in front of them. Agents locate tools via URL env vars (`CLAIMS_TOOLS_URL`,
  `EXPENSE_TOOLS_URL`, `DESK_TOOLS_URL`, `COPILOT_MCP_URL`...) so a proxy can be inserted.

**chat-assistant.** `core/engine.py` runs each turn: route intent (structured) → select tools whose MCP
`meta.intents` match → plan (structured) → tool-use loop with scope enforcement, user confirmation for write
tools, parallel reads → reply; recorded in a `TurnTrace` (`core/trace.py`). Tool metadata (`meta.level`,
`requires_confirmation`, `disclosure`, `step_up_minutes`) is defined in `mcp_server/server.py` and drives host
behaviour. MCP transport is `COPILOT_MCP_TRANSPORT`: `stdio` (subprocess, Docker default), `inprocess` (tests), or
HTTP. `rest_api.py` exposes the same tools as REST. Front ends: `web/app.py` (FastAPI + static UI), `cli.py`.

**claims-agent.** `agent.py`: triage to a route, then a tool loop over MCP streamable HTTP to `tools_server.py`.
Two interchangeable engines, `CLAIMS_ENGINE=loop|langgraph` (`graph.py`), must stay in parity (a test checks it).
`telemetry.py` emits OTel GenAI spans independent of the agent's own account.

**expense-agent.** Same shape as claims-agent (`agent.py` triage then tool loop, `tools_server.py` with
`PrincipalRouter`, `policy.py` scripted policy) but deliberately plain: no LangGraph engine, no OTel, and no host
guards (no `host_refusal`/`procedure_end`/`held`/`stopped`); whatever the model asks for is sent to the tools.
Notices are files under `EXPENSE_OUT/notices/`. The seed reports ER-3001..ER-3010 are a contract (see its README).

**multi-agent-desk.** One image, four roles (`desk.service:triage_app|billing_app|claims_app` factories +
`desk.tools_server`). `identity.py` signs tokens; each handoff re-signs with the chain extended and permissions
narrowed to the receiving role — never widened. Tool server verifies tokens and writes `audit_log`.

**automation-lab.** FastAPI (`app.py`, `create_app(settings)`) serving a no-build static page (`static/`). The page is
authored once as UI release v1; `static/release.js` rewrites ids/names/classes/texts/labels/element types at load
for v2/v3/v4 (`?release=` or `LAB_RELEASE`, `POST /sandbox/release`) and again every N s under live drift
(`?drift=N`, `LAB_DRIFT_SECONDS`, `POST /sandbox/drift`), so recorded locators break and self-healing can be shown.
The per-release rules live in `static/releases.json`, read by both the page (via `/api/config`) and `locators.py`,
which serves `/api/locator-map` (ground truth for scoring heals) — change rules there, never in only one place. App
JS must reach elements via `HM.$(authoredId)`, create them via `HM.h()`, use `HM.C()` for classes, and CSS must never
select by id or `data-testid` — otherwise v2/v3 break. A pytest checks every literal `$("id")` in `app.js` exists in
`index.html`. Hidden pages stay in the DOM, so browser scripts must scope to the visible page region.
`e2e/smoke.py` (Playwright, not in CI) drives every area in v1–v3 and checks the locator map, v4 and drift.

**policy-desk.** C# 5 only (the compiler that ships with Windows): no string interpolation, `?.`, `nameof`,
expression-bodied members or `out var`. `app/`: `Program.cs` (launch options = test hooks, each with a
`POLICYDESK_*` variable), `Domain.cs` (pricing, underwriting, validation, the JSON store `data\policydesk.json` with
its activity log), `Releases.cs` (`ReleaseUi`, the locator map), `Forms.cs` (screens), `SelfTest.cs`. Every control
is created through `ReleaseUi` (`Reg`, `Button`, `Field`, `Menu`) with its authored v1 name, so `releases.json` can
rename it, change its text or type, swap its row, and drift can rename it live. Release rules live only in
`releases.json`; the self-test fails if a rule names an element or text that doesn't exist, or if v2 misses a
control. The locator map must match what native UIA3 really reports (verify with `tools\uia_report.bat` and
`tools\smoke.bat`); the legacy toolbar, Notes box and chart stay unnamed on purpose. Generation inputs are in
`policy-desk/docs/`. The baseline suite (`screens/`, `tests/`, pytest + pywinauto) deliberately uses v1
AutomationIds, so it passes on v1 and breaks on v2–v4. Every planted defect needs a test marked
`@pytest.mark.catches("<defect>")`, and `tools/defect_matrix.py` (which reads the defect list from the app) must pass.

**billing-service and rca-lab.** Design: `docs/rca-agent-test-application.md`. A FastAPI service (`billing/api.py`,
`python -m billing`) with one module per business area (`premiums`, `installments`/`dates`, `payments`, `reminders`,
`renewals`, `refunds`, `rates`, `taxes`), settings in `config/billing.yaml`, rates in `reference/rate_tables.csv`, a
vendored third-party holiday calendar in `billing/_vendor/holidaycal/` (excluded from lint and coverage), and the
portal's money/date formatting and billing page in TypeScript under `portal/`. Business rules are specs in
`docs/specs/` (BR-xx, RF-xx, PD-xx); owners are in `CODEOWNERS`. `BILLING_TODAY` (default `2026-11-03`) and
`BILLING_SANDBOX_TOKEN` work like the other apps'; payments are dated in the billing timezone, never the machine's `TZ`.
Unlike the other apps its defects are **not** env-flag switches: they were slipped into ordinary feature commits
between the release tags `billing-service-v1.0.0` (clean) → `v1.1.0` → `v1.2.0` → `v1.3.0`, so the RCA agent can find them
with `git log`/`blame`. `rca-lab/security_audit.py` is the dependency audit the `security` tickets are accepted against.
Hence:
- `main`'s history is append-only (never rewrite, rebase or force-push), and don't "fix" a planted defect, add a test
  that catches one, or describe one in a commit message, comment or doc. The shipped suite and CI stay green with the
  defects in place. A new release needs a version bump (`pyproject.toml`, `CHANGELOG.md`) and a
  `billing-service-v<version>` tag.
- The repo is **public** and the agent reads it: the answer key and hidden tests live only in the git-ignored
  `rca-lab/private/` (or `RCA_LAB_KEY_PATH`) and are never committed. `rca-lab/tickets/*.md` are the public ticket
  texts (filed as GitHub issues labelled `rca-lab`); `selfcheck.py` proves each key entry against the tagged
  history in a temporary worktree.

**Planted defects.** `CLAIMS_DEFECTS` / `EXPENSE_DEFECTS` / `DESK_DEFECTS` / `LAB_DEFECTS` / `POLICYDESK_DEFECTS` (comma-separated; see `defects.py` in each app, `app/Defects.cs` in policy-desk) switch on
deliberate bugs so TruAssure can prove it catches them (billing-service is the exception, see above). Unknown names are errors. They must stay off by default
and keep doing exactly what their READMEs describe. The chat assistant also has two known bugs pinned as strict
`xfail` tests — fixing one will flip the xfail to a failure, so update the test deliberately.

## Conventions

- Each project has its **own ruff settings**; chat-assistant intentionally uses only ruff's default rules
  (`E4,E7,E9,F`) to keep its original style. Don't run `ruff format` or apply one app's style to another.
- Keep the 95% coverage gate green in every project.
- Ports bind to `127.0.0.1` only; secrets live only in `.env` (git-ignored) and are never baked into images.
  `DEMO_USER_PASSWORD` must be set for the chat assistant to start (tests set a default in `conftest.py`).
- Docker data (`<app>/data/`: DBs, letters, logs, signing key) is git-ignored and read from the host by TruAssure.
