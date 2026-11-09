# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Demo "systems under test" (SUT) for the fictitious insurer **Harbor Mutual**, which the separate TruAssure project
tests **black-box over HTTP** (it never imports this code). Four independent Python apps plus one shared library:

| Dir | Package | What it is | Ports |
|---|---|---|---|
| `common/harbor_llm` | `harbor_llm` (src layout) | Shared LLM factory used by every app | — |
| `chat-assistant` | `insurance_copilot` | Customer chat: intent routing → plan → MCP tool loop, SQLite | 8000 |
| `claims-agent` | `claims_agent` | Event-driven back-office agent, no chat; agent + MCP tool server | 8100, 9100 |
| `multi-agent-desk` | `desk` | Triage → billing → claims agents with signed identity propagation | 8300 (triage), 9300 (tools) |
| `automation-lab` | `automation_lab` | No-login web UI for test-script generation/execution/self-healing; **no LLM**, no `harbor_llm` dependency | 8400 |

Because TruAssure tests these apps, **behaviour visible over HTTP, tool names/arguments, seed data, response
shapes and the planted defects are a contract**. CI publishes `ghcr.io/<owner>/harbor-{claims-agent,member-desk,chat-assistant,automation-lab}`
tagged `sha-<commit>` and `latest` on every green push to `main`; TruAssure pins a `sha-` tag.

## Commands

Each app is its own project; run commands from the app's directory. Python 3.12 (>=3.11 supported). Install the
shared factory first (editable, from the sibling path):

```bat
cd chat-assistant            :: or claims-agent / multi-agent-desk
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ..\common\harbor_llm[all] -r requirements.txt pytest pytest-asyncio pytest-cov httpx ruff
```

| Project | Lint (as CI runs it) | Tests (95% branch-coverage gate) |
|---|---|---|
| `common/harbor_llm` | `ruff check src tests` and `mypy` (strict) | `python -m pytest -q --cov` (install with `-e ".[all,dev]"`) |
| `chat-assistant` | `ruff check insurance_copilot tests` | `python -m pytest -q --cov --cov-fail-under=95` |
| `claims-agent` | `ruff check claims_agent tests` | `python -m pytest -q --cov` |
| `multi-agent-desk` | `ruff check desk tests` | `python -m pytest -q --cov` |
| `automation-lab` | `ruff check automation_lab tests` | `python -m pytest -q --cov` (install: `-r requirements.txt pytest pytest-cov httpx ruff`, no `harbor_llm`) |

Single test: `python -m pytest tests/test_engine.py::test_name -q`. All tests are offline (scripted LLM, no keys);
pytest uses `asyncio_mode = "auto"`, so async tests need no decorator.

Docker (from the **repo root**; build context is the root so `common/harbor_llm` can be copied in):

```bat
docker compose -f claims-agent\docker-compose.yml up -d --build
docker compose -f claims-agent\docker-compose.yml -f claims-agent\docker-compose.observed.yml up -d --build   :: with TruAssure's recording proxy
```

## Architecture notes

**LLM factory (`harbor_llm`).** `make_llm(override_var=...)` picks a provider from `<APP>_LLM_PROVIDER`
(`CHAT_`/`CLAIMS_`/`DESK_`) or `DEFAULT_LLM_PROVIDER`, read from the root `.env` (copy `.env.example`). Every
provider is normalized to one Anthropic-style interface: `await llm.create(messages, system, tools, ...)` returning
`LLMResponse` of `TextBlock`/`ToolUseBlock`, and `await llm.structured(system, user, schema=PydanticModel)`. Apps
code only against this interface. The `SCRIPTED` provider is how everything runs offline:
`LLM_SCRIPT=pkg.module:factory` loads an app-supplied rule-based policy (`claims_agent.policy:factory`,
`desk.policy:factory`), or `LLM_SCRIPT_FILE` replays a JSONL of recorded responses.

**Common app shape.** Each app = an agent loop + an MCP tool server over a SQLite system of record, with:
- a fixed "today" (`*_TODAY`, default `2026-11-03`) so outputs are reproducible;
- token-protected `/sandbox/status|reset` (and `/sandbox/faults` in the desk) test-control endpoints that refuse
  everything when the token env var is empty;
- the principal passed in a header by the host, never chosen by the model (`X-Customer-Id` for chat,
  `X-Principal` for claims and desk); the tool server enforces that principal's permissions;
- a returned "account" of the episode (route, steps/tool calls, output, done) that TruAssure compares against
  independent records (proxy logs, OTel spans, DB diffs);
- `docker-compose.observed.yml` that splits tools into their own service and puts TruAssure's proxy image
  (`TRUASSURE_PROXY_IMAGE`) in front of them. Agents locate tools via URL env vars (`CLAIMS_TOOLS_URL`,
  `DESK_TOOLS_URL`, `COPILOT_MCP_URL`...) so a proxy can be inserted.

**chat-assistant.** `core/engine.py` runs each turn: route intent (structured) → select tools whose MCP
`meta.intents` match → plan (structured) → tool-use loop with scope enforcement, user confirmation for write
tools, parallel reads → reply; recorded in a `TurnTrace` (`core/trace.py`). Tool metadata (`meta.level`,
`requires_confirmation`, `disclosure`, `step_up_minutes`) is defined in `mcp_server/server.py` and drives host
behaviour. MCP transport is `COPILOT_MCP_TRANSPORT`: `stdio` (subprocess, Docker default), `inprocess` (tests), or
HTTP. `rest_api.py` exposes the same tools as REST. Front ends: `web/app.py` (FastAPI + static UI), `cli.py`.

**claims-agent.** `agent.py`: triage to a route, then a tool loop over MCP streamable HTTP to `tools_server.py`.
Two interchangeable engines, `CLAIMS_ENGINE=loop|langgraph` (`graph.py`), must stay in parity (a test checks it).
`telemetry.py` emits OTel GenAI spans independent of the agent's own account.

**multi-agent-desk.** One image, four roles (`desk.service:triage_app|billing_app|claims_app` factories +
`desk.tools_server`). `identity.py` signs tokens; each handoff re-signs with the chain extended and permissions
narrowed to the receiving role — never widened. Tool server verifies tokens and writes `audit_log`.

**automation-lab.** FastAPI (`app.py`, `create_app(settings)`) serving a no-build static page (`static/`). The page is
authored once as UI release v1; `static/release.js` rewrites ids/names/classes/texts/layout at load for v2/v3
(`?release=` or `LAB_RELEASE`, `POST /sandbox/release`) so recorded locators break and self-healing can be shown. App
JS must reach elements via `HM.$(authoredId)`, create them via `HM.h()`, use `HM.C()` for classes, and CSS must never
select by id or `data-testid` — otherwise v2/v3 break. A pytest checks every literal `$("id")` in `app.js` exists in
`index.html`. Hidden pages stay in the DOM, so browser scripts must scope to the visible page region.
`e2e/smoke.py` (Playwright, not in CI) drives every area in all three releases.

**Planted defects.** `CLAIMS_DEFECTS` / `DESK_DEFECTS` / `LAB_DEFECTS` (comma-separated; see `defects.py` in each app) switch on
deliberate bugs so TruAssure can prove it catches them. Unknown names are errors. They must stay off by default
and keep doing exactly what their READMEs describe. The chat assistant also has two known bugs pinned as strict
`xfail` tests — fixing one will flip the xfail to a failure, so update the test deliberately.

## Conventions

- Each project has its **own ruff settings**; chat-assistant intentionally uses only ruff's default rules
  (`E4,E7,E9,F`) to keep its original style. Don't run `ruff format` or apply one app's style to another.
- Keep the 95% coverage gate green in every project.
- Ports bind to `127.0.0.1` only; secrets live only in `.env` (git-ignored) and are never baked into images.
  `DEMO_USER_PASSWORD` must be set for the chat assistant to start (tests set a default in `conftest.py`).
- Docker data (`<app>/data/`: DBs, letters, logs, signing key) is git-ignored and read from the host by TruAssure.
