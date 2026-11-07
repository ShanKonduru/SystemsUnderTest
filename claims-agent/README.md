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
docker compose -f SUT\claims-agent\docker-compose.yml up -d --build
curl -X POST http://127.0.0.1:8100/events -H "X-Principal: adjuster-07" -H "Content-Type: application/json" ^
     -d "{\"event\": {\"type\": \"claim.document_received\", \"claim_id\": \"CLM-5003\", \"document_id\": \"DOC-78\"}}"
docker compose -f SUT\claims-agent\docker-compose.yml down
```

The response is the agent's own account of the episode: `route`, every tool call (`steps`, with arguments and
outcome), `output`, `done` and token `usage`. The database and letters are in `SUT/claims-agent/data/`, which
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

## Develop and test

```bat
cd SUT\claims-agent
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ..\common\harbor_llm[all] -r requirements.txt pytest pytest-asyncio pytest-cov httpx ruff
.venv\Scripts\python -m pytest -q --cov
```

The 30 offline tests run at 98.7% coverage, with a 95% gate. They drive the real MCP tool server, in-process
and over HTTP, with the rule-based policy. The procedures above have also been verified live with Azure OpenAI.
