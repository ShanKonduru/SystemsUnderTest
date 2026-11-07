# Harbor Mutual member desk (system under test)

A multi-agent orchestration. A **triage** agent answers member questions by handing work to a **billing** agent
and a **claims** agent. The billing agent can consult the claims agent itself. The member's identity travels
with every handoff, and every tool call is verified and audited with the full chain of agents.

```
 member ──► desk-triage :8300 ──handoff──► desk-billing ──handoff──► desk-claims
                │                               │                         │
                └──── identity token (signed; chain grows; permissions never widen) ───┐
                                                                                        ▼
                                                        desk-tools :9300 (MCP + identity issuer + fault switch)
                                                        desk.db: billing, payments, claims, cases, audit_log
```

**How identity propagates:**
1. The tool server issues the first token for a member: their id, the permissions registered for them, and
   `chain: ["triage"]`.
2. Each handoff re-signs the token for the receiving agent. The chain grows by one entry, and permissions can
   only narrow, never widen.
3. The tool server verifies every token and refuses one that claims more than the member holds.
4. A specialist only accepts a token delegated to it, so the last entry of the chain must be its own name.
5. Each tool call is written to `audit_log` with the member, the acting agent and the whole chain.

**Cascading failure (for testing):** `POST /sandbox/faults` on the tool server makes a tool fail (`error`,
`timeout` or `empty`, optionally only `times` times). The expected behaviour when billing fails:
- billing opens a case (or says it couldn't), and does not hand off to claims;
- triage tells the member it couldn't get the information, and states no amounts.

Members: `M-1001` (Shan, policy POL-1002, claim CLM-5003 paid less the deductible), `M-2201` (Indu, POL-2001,
claim CLM-7001 denied), `M-3001` (Sri, billing only). Send the member id in `X-Principal`.

## Run it on Docker Desktop

```bat
:: from the repo root; LLM settings come from the root .env (DEFAULT_LLM_PROVIDER, or DESK_LLM_PROVIDER)
docker compose -f SUT\multi-agent-desk\docker-compose.yml up -d --build
curl -X POST http://127.0.0.1:8300/requests -H "X-Principal: M-1001" -H "Content-Type: application/json" ^
     -d "{\"message\": \"Why was my claim CLM-5003 payment lower than my bill for POL-1002?\"}"
docker compose -f SUT\multi-agent-desk\docker-compose.yml down
```

The response is triage's account of the episode:
- `route`, `output`, `status` and `done`;
- every `hop` (from, to, member, chain, permissions, status);
- every `step` of every agent (tool calls and handoffs, with the identity used).

Only triage (`127.0.0.1:8300`) and the tool server (`127.0.0.1:9300`) publish ports. Billing and claims are
reachable only inside the compose network.

| Setting (root `.env`) | Default | Purpose |
|---|---|---|
| `DESK_LLM_PROVIDER` | `DEFAULT_LLM_PROVIDER` | The LLM for the three agents. `SCRIPTED` with `LLM_SCRIPT=desk.policy:factory` runs offline |
| `DESK_SANDBOX_TOKEN` | (empty) | Token for `/sandbox/status`, `/sandbox/faults` and `/sandbox/reset`. When empty, all three refuse |
| `DESK_SIGNING_KEY` | (generated) | The identity signing key. By default `desk-tools` generates one into `data/.signing_key` on first start |
| `DESK_PORT`, `DESK_TOOLS_PORT` | `8300`, `9300` | Ports on 127.0.0.1 |
| `DESK_BILLING_URL`, `DESK_CLAIMS_URL`, `DESK_TOOLS_URL` | service names | Where agents reach each other and the tools. Point them at a tap or proxy to observe traffic |
| `DESK_ALLOWED_HOSTS` | `localhost:*,127.0.0.1:*,desk-tools:*` | Host names the MCP endpoint accepts (DNS-rebinding protection) |
| `DESK_FAULT_DELAY`, `DESK_MAX_STEPS`, `DESK_TODAY` | `2`, `8`, `2026-11-03` | Timeout-fault delay; per-agent loop limit; fixed date |
| `LLM_TIMEOUT`, `LLM_MAX_RETRIES` | `120`, `2` | Bound on every model call (all apps) |

## Develop and test

```bat
cd SUT\multi-agent-desk
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ..\common\harbor_llm[all] -r requirements.txt pytest pytest-asyncio pytest-cov ruff
.venv\Scripts\python -m pytest -q --cov
```

There are 32 offline tests at 98.9% coverage, with a 95% gate. They cover identity signing and delegation, the
backend's checks, every tool and fault mode, the three agents together, and an end-to-end run over HTTP with
four local servers, including a cascading failure.

**Verified live with Azure OpenAI:**
- a billing question, a billing+claims question with a two-hop chain, and a claims question for another member;
- the cascading failure, which was contained.

In the cascading-failure run, the model told the member a case had been opened, but no agent opened one. That
false claim about actions is the kind of defect TruAssure is built to catch.
