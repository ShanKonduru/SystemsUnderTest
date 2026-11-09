# Harbor Mutual claims task agent (system under test)

An event-driven back-office agent with **no chat**. Back-office systems post events. The agent decides which
procedure applies, works through MCP tools against the claims system of record, and reports what it did.

```
 back-office event ──► claims-agent (FastAPI :8100) ──MCP over HTTP──► claims-tools (:9100) ──► claims.db, letters/
                         triage + tool loop (any LLM)                   one server per principal, permissions enforced
```

| Event `type` | Procedure (route) | Expected behaviour |
|---|---|---|
| `claim.document_received` | `claims_intake` | Open claim → `under_review` + acknowledgement letter. Any other status → add a note only |
| `claim.assessment_completed` | `claims_settlement` | Approve with the amount and an approval letter, or deny with a reason and a denial letter. **Never pays** |
| `claim.payment_due` | `claims_payment` | Pay the approved amount. Needs `payments:write`, so only `supervisor-02` succeeds; others get a note |
| anything else | `unsupported` | No action |

Principals (sent in the `X-Principal` header): `adjuster-07` (read, update, letters), `supervisor-02` (adds
payments), `intake-bot` (read only). Seed claims: CLM-5001 (approved), CLM-5003 (open), CLM-5004 (under review),
CLM-5006 (open, on a lapsed policy).

## Run it on Docker Desktop

```bat
:: from the repo root; LLM settings come from the root .env (DEFAULT_LLM_PROVIDER, or CLAIMS_LLM_PROVIDER)
docker compose -f claims-agent\docker-compose.yml up -d --build
curl -X POST http://127.0.0.1:8100/events -H "X-Principal: adjuster-07" -H "Content-Type: application/json" ^
     -d "{\"event\": {\"type\": \"claim.document_received\", \"claim_id\": \"CLM-5003\", \"document_id\": \"DOC-78\"}}"
docker compose -f claims-agent\docker-compose.yml down
```

The response is the agent's own account of the episode: `route`, every tool call (`steps`, with arguments and
outcome), `output`, `done` and token `usage`. The database and letters are in `claims-agent/data/`, which
git ignores, so you can inspect them from the host.

| Setting (root `.env`) | Default | Purpose |
|---|---|---|
| `CLAIMS_LLM_PROVIDER` | `DEFAULT_LLM_PROVIDER` | The LLM for this app. `SCRIPTED` with `LLM_SCRIPT=claims_agent.policy:factory` runs offline with a rule-based policy |
| `CLAIMS_PORT`, `CLAIMS_TOOLS_PORT` | `8100`, `9100` | Ports on 127.0.0.1 |
| `CLAIMS_TOOLS_URL` | `http://claims-tools:9100/mcp` | Where the agent reaches the tools. Point it at a recording proxy to observe tool traffic |
| `CLAIMS_TOOLS_TOKEN` | (empty) | If set, the tool server requires `Authorization: Bearer <token>` |
| `CLAIMS_TOOLS_ALLOWED_HOSTS` | `localhost:*,127.0.0.1:*,claims-tools:*` | Host names the tool server accepts. DNS-rebinding protection stays on; add a proxy's name if you use one |
| `CLAIMS_SANDBOX_TOKEN` | (empty) | Token for `/sandbox/status` and `/sandbox/reset`. When empty, both refuse every request |
| `CLAIMS_TODAY`, `CLAIMS_MAX_STEPS` | `2026-11-03`, `10` | Fixed date; tool-loop limit |
| `CLAIMS_OTEL_FILE` | `/app/data/otel/spans.jsonl` in Docker | Append the OpenTelemetry GenAI spans of each episode (OTLP/JSON); `data/otel/` on the host |
| `CLAIMS_OTEL_ENDPOINT`, `CLAIMS_OTEL_TOKEN` | (empty) | Also post the spans to an OTLP/HTTP receiver (e.g. `ta otel`), with a bearer token |
| `CLAIMS_DEFECTS` | (empty) | **Test runs only.** A "buggy build": comma-separated planted defects (below) |
| `CLAIMS_ENGINE` | `loop` | How the tool loop runs: `loop` (plain Python) or `langgraph` (a LangGraph graph). Two builds of the same agent, for parity testing (SE-06); `/healthz` reports it |

## Telemetry

Each episode emits one `invoke_agent` span and one `execute_tool` span per tool call, with the GenAI
semantic-convention attributes (`gen_ai.operation.name`, `gen_ai.tool.name`, `gen_ai.tool.call.id`,
`gen_ai.tool.call.arguments`) and the principal as `enduser.id`. The spans record what the agent actually
called, independently of the account it returns. Exporting never breaks an episode.

## Planted defects (buggy build)

`CLAIMS_DEFECTS` switches on defects that copy real failure classes, so a test framework can show it catches
them ([`claims_agent/defects.py`](claims_agent/defects.py)). An unknown name is an error.

| Defect | Where | What it does |
|---|---|---|
| `silent_write` | tool server | `get_claim` (a read) also writes a claim note |
| `phantom_note` | tool server | `add_note` reports success but saves nothing |
| `pay_on_settlement` | scripted model | the settlement procedure also pays the claim |
| `loop` | scripted model | the agent keeps re-reading the claim and never finishes |
| `vague_letter` | scripted model | acknowledgement letters leave out the claim and the document |
| `trace_omits_read` | agent | the agent's own account leaves out its `get_claim` calls (the spans don't) |

## Develop and test

```bat
cd claims-agent
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ..\common\harbor_llm[all] -r requirements.txt pytest pytest-asyncio pytest-cov httpx ruff
.venv\Scripts\python -m pytest -q --cov
```

The 39 offline tests run at 99.2% coverage, with a 95% gate; one checks that both engines give the same account of every procedure. They drive the real MCP tool server, in-process
and over HTTP, with the rule-based policy. The procedures above have also been verified live with Azure OpenAI.
