# Harbor Mutual chat assistant (system under test)

A customer-facing insurance chat assistant for the fictitious insurer Harbor Mutual. Each customer message goes
through intent routing, a plan, and tool calls through an MCP tool server, against a SQLite system of record.
Actions that change data, such as payments or policy changes, ask the customer for approval first.

## Run it on Docker Desktop

```bat
:: from the repo root; LLM settings come from the root .env (see ..\..\.env.example)
docker compose -f SUT\chat-assistant\docker-compose.yml up -d --build
:: open http://127.0.0.1:8000  -  demo customers: shan, indu, sri (password: DEMO_USER_PASSWORD in the root .env)
docker compose -f SUT\chat-assistant\docker-compose.yml logs -f
docker compose -f SUT\chat-assistant\docker-compose.yml down        :: add -v to also delete the database volume
```

| Setting (root `.env`) | Default | Purpose |
|---|---|---|
| `DEFAULT_LLM_PROVIDER` / `CHAT_LLM_PROVIDER` | (required) | Which LLM to use; `CHAT_LLM_PROVIDER` overrides for this app only |
| `CHAT_PORT` | `8000` | Port on 127.0.0.1 |
| `CHAT_TODAY` | `2026-11-03` | Fixed "today", so dates in answers are reproducible |
| `CHAT_SANDBOX_TOKEN` | (empty) | Token for the test-control endpoints `/sandbox/status` and `/sandbox/reset`; empty refuses every request |

The port is bound to `127.0.0.1` only. The database is `SUT/chat-assistant/data/insurance.db` (a bind mount,
git-ignored), created and seeded on first start; TruAssure reads it read-only to diff state.

## Run it observed (what TruAssure's pilot 0 uses)

```bat
docker compose -f SUT\chat-assistant\docker-compose.yml -f SUT\chat-assistant\docker-compose.observed.yml up -d --build
```

The assistant's MCP tools then run as their own service (`chat-tools`, MCP over HTTP) behind TruAssure's recording
proxy (`chat-proxy`, control API on 127.0.0.1:9450). Every tool call is logged to `data/proxy-logs/` with the customer
from the `X-Customer-Id` header, and faults can be injected with `TA_PROXY_TOKEN`. The tools server accepts the Host
names in `COPILOT_MCP_ALLOWED_HOSTS` (default `localhost:*,127.0.0.1:*,chat-tools:*`). `scripts\truassure-ops.bat
deploy chat` deploys it this way.

## Develop and test locally

```bat
cd SUT\chat-assistant
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ..\common\harbor_llm[all] -r requirements.txt pytest pytest-asyncio pytest-cov httpx
.venv\Scripts\python -m pytest -q
```

The 208 offline tests (99% line-and-branch coverage, 95% gate) use a scripted fake LLM, so they need no keys. Two known app bugs are pinned as strict expected failures (`xfail`): a planner failure crashes the turn, and the REST RPC fallback leaks its `{tool}` path segment into the tool arguments. The app gets its LLM from the shared factory
([`../common/harbor_llm`](../common/harbor_llm)), so any provider configured in `.env` works.
