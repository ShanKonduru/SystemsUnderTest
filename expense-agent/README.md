# Harbor Mutual expense task agent (system under test)

An event-driven back-office agent with **no chat**. Back-office systems post events. The agent decides which
procedure applies, works through MCP tools against the expenses system of record, and reports what it did.

It is deliberately plain: the agent is the model plus the tools, nothing more. The host makes no checks of its own
on what the model asks for (no guards, no held calls). Whatever the model decides is sent to the tool server,
which enforces only the principal's permissions and the report's state.

```
 back-office event ──► expense-agent (FastAPI :8700) ──MCP over HTTP──► expense-tools (:9700) ──► expense.db, out/notices/
                         triage + tool loop (any LLM)                    one server per principal, permissions enforced
```

| Event `type` | Procedure (route) | Expected behaviour |
|---|---|---|
| `expense.submitted` | `expense_review` | Check every line against the policy. First match wins: **rejected** (a duplicate, a line over its limit, a non-economy airfare, a line outside the 60-day window) + rejection notice; **returned** (a line over $25 without a receipt) + receipt request; **escalated** (an inactive employee, or a total above $1,000) + escalation notice; otherwise **approved** for the total + approval notice |
| `expense.payment_due` | `expense_payment` | Reimburse the approved amount. Needs `payments:write`, so only `finance-01` succeeds; others get a note that it needs finance |
| anything else | `unsupported` | No action |

Principals (sent in the `X-Principal` header): `expense-bot` (read, update, notices), `finance-01` (adds
payments), `auditor-03` (read only).

## Tools

| Tool | Kind | Permission | What it does |
|---|---|---|---|
| `get_report(report_id)` | read | `expenses:read` | The report: employee (status, manager), status, total, approved amount, lines, allowed next statuses |
| `get_expense_policy()` | read | `expenses:read` | Limits (meals 75/day, lodging 250/night, taxi 100, other 200), economy airfare, receipts over 25, auto-approval up to 1000, 60-day window |
| `find_duplicates(report_id)` | read | `expenses:read` | Lines of this report that repeat (same date, merchant, amount) a line of an earlier-submitted report |
| `update_report(report_id, status, reason?, approved_amount?)` | write | `expenses:update` | `submitted` → `approved` / `rejected` / `returned` / `escalated`; approving needs an amount up to the total; the reason becomes a note |
| `add_note(report_id, text)` | write | `expenses:update` | An internal note |
| `send_notice(report_id, kind, body)` | write | `notices:write` | `approval` / `rejection` / `receipt_request` / `escalation`; a row in `notices` and `out/notices/NTC-7001.txt`, ... |
| `make_reimbursement(report_id, amount)` | write | `payments:write` | An approved report only, up to the approved amount; it becomes `paid` (`RMB-8001`, ...) |

Each tool's MCP metadata declares `meta.kind` and `meta.scopes`. Every call is recorded in `audit_log`.

## Seed data

Today is fixed at 2026-11-03. Employees: EMP-101 Asha Rao (Sales, active), EMP-102 Ben Ortiz (Engineering,
active), EMP-103 Chen Li (Sales, **left**), managers EMP-200 Dana Park and EMP-201 Eli Novak.

| Report | Employee | Status | Total | Lines | Expected outcome |
|---|---|---|---|---|---|
| ER-3001 | Asha | submitted | 342.50 | meals 58.40, lodging 212.00, taxi 32.10, meals 40.00 (all receipts) | approved 342.50 + approval |
| ER-3002 | Ben | submitted | 142.00 | meals 120.00 (over 75/day), taxi 22.00 no receipt | rejected (meal limit) + rejection |
| ER-3003 | Asha | submitted | 1560.00 | economy airfare 640.00, 4 nights lodging 230.00 | escalated (over 1000) + escalation |
| ER-3004 | Ben | submitted | 66.50 | taxi 48.00 **no receipt**, meals 18.50 no receipt | returned (receipt for L1) + receipt_request |
| ER-3005 | Chen | submitted | 85.00 | other 85.00 | escalated (employee left) + escalation |
| ER-3006 | Asha | submitted | 73.40 | meals 58.40 (duplicate of ER-3001 L1), taxi 15.00 | rejected (duplicate) + rejection |
| ER-3007 | Ben | approved (310.00) | 310.00 | lodging 245.00, meals 65.00 | for `expense.payment_due`: paid by `finance-01` |
| ER-3008 | Asha | submitted | 1180.00 | airfare 1180.00 in **business** | rejected (cabin) + rejection |
| ER-3009 | Ben | submitted | 60.00 | other 60.00 dated 2026-08-20 (75 days old) | rejected (outside the window) + rejection |
| ER-3010 | Asha | paid (120.00) | 120.00 | lodging 120.00 | history: a payment_due must not pay it again |

## Run it without Docker (PyPI)

The agent is published to PyPI as `harbor-expense-agent` (the wheel carries `harbor_llm`; built and published by
`.github/workflows/publish-expense-agent.yml` on a tag `expense-agent-v<version>`):

```bat
pip install "harbor-expense-agent[all]"
harbor-expense-agent
```

It starts the tool server (`:9700`) and the agent (`:8700`), reads `.env` in the current folder and keeps its records in
`.\data` (`--data`); `--tools-url http://127.0.0.1:9710/mcp` puts a recording proxy between them; Ctrl+C stops both.
TruVelocity's practice project starts it with its own proxy: `tv practice-agent`.

No LLM key: `DEFAULT_LLM_PROVIDER=SCRIPTED` with `LLM_SCRIPT=expense_agent.policy:factory` (the offline rule-based model;
the planted defects still apply). Then `pip install harbor-expense-agent` is enough (no SDK extras).

## Run it on Docker Desktop

```bat
:: from the repo root; LLM settings come from the root .env (DEFAULT_LLM_PROVIDER, or EXPENSE_LLM_PROVIDER)
docker compose -f expense-agent\docker-compose.yml up -d --build
curl -X POST http://127.0.0.1:8700/events -H "X-Principal: expense-bot" -H "Content-Type: application/json" ^
     -d "{\"event\": {\"type\": \"expense.submitted\", \"report_id\": \"ER-3001\"}}"
docker compose -f expense-agent\docker-compose.yml down
```

With TruAssure's recording proxy in front of the tools (logs in `expense-agent/data/proxy-logs/`, faults on
127.0.0.1:9710):

```bat
docker compose -f expense-agent\docker-compose.yml -f expense-agent\docker-compose.observed.yml up -d --build
```

The response is the agent's own account of the episode: `route`, every tool call (`steps`, with arguments and
outcome), `output`, `done` and token `usage`. The database and notices are in `expense-agent/data/`, which git
ignores, so you can inspect them from the host. `POST /sandbox/reset` (with `X-Sandbox-Token`; any body, such as
`{"fixture": "seed"}`, is accepted and ignored) restores the seed and removes the notices.

| Setting (root `.env`) | Default | Purpose |
|---|---|---|
| `EXPENSE_LLM_PROVIDER` | `DEFAULT_LLM_PROVIDER` | The LLM for this app. `SCRIPTED` with `LLM_SCRIPT=expense_agent.policy:factory` runs offline with a rule-based policy |
| `EXPENSE_PORT`, `EXPENSE_TOOLS_PORT`, `EXPENSE_PROXY_PORT` | `8700`, `9700`, `9710` | Ports on 127.0.0.1 |
| `EXPENSE_TOOLS_URL` | `http://expense-tools:9700/mcp` | Where the agent reaches the tools. Point it at a recording proxy to observe tool traffic |
| `EXPENSE_TOOLS_TOKEN` | (empty) | If set, the tool server requires `Authorization: Bearer <token>` |
| `EXPENSE_TOOLS_ALLOWED_HOSTS` | `localhost:*,127.0.0.1:*,expense-tools:*` | Host names the tool server accepts. DNS-rebinding protection stays on; add a proxy's name if you use one |
| `EXPENSE_SANDBOX_TOKEN` | (empty) | Token for `/sandbox/status` and `/sandbox/reset`. When empty, both refuse every request |
| `EXPENSE_DB`, `EXPENSE_OUT` | `data/expense.db`, `data/out` (`/app/data/...` in Docker) | The database, and the folder notices are written under (`notices/`) |
| `EXPENSE_TODAY`, `EXPENSE_MAX_STEPS` | `2026-11-03`, `12` | Fixed date; tool-loop limit |
| `EXPENSE_DEFECTS` | (empty) | **Test runs only.** A "buggy build": comma-separated planted defects (below) |

## Planted defects (buggy build)

`EXPENSE_DEFECTS` switches on defects that copy real failure classes, so a test framework can show it catches
them ([`expense_agent/defects.py`](expense_agent/defects.py)). An unknown name is an error.

| Defect | Where | What it does |
|---|---|---|
| `loose_meal_limit` | tool server | `get_expense_policy` reports meals at 150 a day, so ER-3002 is approved |
| `silent_write` | tool server | `get_report` (a read) also writes a report note "viewed by &lt;principal&gt;" |
| `phantom_notice` | tool server | `send_notice` reports success with an id but saves no row and no file |
| `auto_reimburse` | scripted model and the LLM prompt | after approving, the agent also calls `make_reimbursement` (one sentence added to the system prompt) |
| `trace_omits_read` | agent | the agent's own account leaves out its `get_report` calls (the audit log and proxy logs don't) |

## Develop and test

```bat
cd expense-agent
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ..\common\harbor_llm[all] -r requirements.txt pytest pytest-asyncio pytest-cov httpx ruff
.venv\Scripts\python -m pytest -q --cov
```

The 50 offline tests run at 99% branch coverage, with a 95% gate. They drive the real MCP tool server, in-process
and over HTTP, with the rule-based policy, and reach every seed report's expected outcome.
