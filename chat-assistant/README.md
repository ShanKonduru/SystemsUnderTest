# Harbor Mutual chat assistant (system under test)

A customer-facing insurance chat assistant for the fictitious insurer Harbor Mutual. Each customer message goes
through intent routing, a plan, and tool calls through an MCP tool server, against a SQLite system of record.
Actions that change data, such as payments or policy changes, ask the customer for approval first.

## Run it on Docker Desktop

```bat
:: from the repo root; LLM settings come from the root .env (see ..\..\.env.example)
docker compose -f SUT\chat-assistant\docker-compose.yml up -d --build
:: open http://127.0.0.1:8000  -  demo customers: shan, indu, sri (password P@$$w0rd123, demo data only)
docker compose -f SUT\chat-assistant\docker-compose.yml logs -f
docker compose -f SUT\chat-assistant\docker-compose.yml down        :: add -v to also delete the database volume
```

| Setting (root `.env`) | Default | Purpose |
|---|---|---|
| `DEFAULT_LLM_PROVIDER` / `CHAT_LLM_PROVIDER` | (required) | Which LLM to use; `CHAT_LLM_PROVIDER` overrides for this app only |
| `CHAT_PORT` | `8000` | Port on 127.0.0.1 |
| `CHAT_TODAY` | `2026-11-03` | Fixed "today", so dates in answers are reproducible |
| `CHAT_SANDBOX_TOKEN` | (empty) | Token for the test-control endpoints `/sandbox/status` and `/sandbox/reset`; empty refuses every request |

The port is bound to `127.0.0.1` only. The database lives in the `chat-data` volume and is created and
seeded on first start.

## Develop and test locally

```bat
cd SUT\chat-assistant
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ..\common\harbor_llm[all] -r requirements.txt pytest pytest-asyncio pytest-cov httpx
.venv\Scripts\python -m pytest -q
```

The 120 offline tests use a scripted fake LLM, so they need no keys. The app gets its LLM from the shared factory
([`../common/harbor_llm`](../common/harbor_llm)), so any provider configured in `.env` works.
