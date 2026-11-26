# Systems under test (SUT)

Demo applications that TruAssure tests. They belong to the fictitious insurer **Harbor Mutual**; no real
customer or company data is used anywhere. Each app has its own `docker-compose.yml` for Docker Desktop.

These apps used to live in TruAssure's `SUT/` folder (their history came along). TruAssure never imports their code:
it tests them black-box, over HTTP, like any client's agent.

- **Images:** on every push to `main` that passes CI, this repository publishes `ghcr.io/shankonduru/harbor-claims-agent`,
  `harbor-expense-agent`, `harbor-member-desk`, `harbor-chat-assistant`, `harbor-automation-lab` and `harbor-billing-service`, tagged `sha-<commit>` and `latest`. TruAssure's offline pilot tests
  pin a `sha-` tag and run those images with a scripted model (no LLM).
- **Live demos:** TruAssure's `scripts/truassure-ops.*` deploy these apps from a clone of this repository next to
  TruAssure's (or wherever `TA_SUT_DIR` points), with TruAssure's `.env`. The observed deployments
  (`docker-compose.observed.yml`) put TruAssure's recording proxy in front of an app's tools; the scripts build that
  image (`truassure/proxy:dev`) from TruAssure's source first.

| App | What it is | Local URL |
|---|---|---|
| [`chat-assistant`](chat-assistant) | Customer chat assistant (login, MCP tools, LLM) | http://127.0.0.1:8000 |
| [`claims-agent`](claims-agent) | Event-driven back-office claims agent (no UI) | http://127.0.0.1:8100 |
| [`expense-agent`](expense-agent) | Event-driven back-office expense agent (no UI): plain model plus MCP tools | http://127.0.0.1:8700 |
| [`multi-agent-desk`](multi-agent-desk) | Triage, billing and claims agents with identity propagation | http://127.0.0.1:8300 |
| [`automation-lab`](automation-lab) | Web UI for test-script generation, execution and self-healing (no login, no LLM) | http://127.0.0.1:8400 |
| [`policy-desk`](policy-desk) | Windows desktop app (WinForms) for desktop automation, script generation and self-healing (no LLM, no image) | `policy-desk\start_policy_desk.bat` |
| [`billing-service`](billing-service) | Billing service (premiums, installments, payments, renewals, refunds) and the member portal's billing page, in Python and TypeScript; the RCA agent's target (no LLM) | http://127.0.0.1:8500/portal?policy=P-5001 |

## RCA lab

[`rca-lab`](rca-lab) tests an RCA (root-cause analysis) agent against `billing-service`. Its defects are planted in
ordinary feature commits between the release tags `billing-service-v1.0.0`, `v1.1.0` and `v1.2.0`. The agent reads
a ticket, the logs and the code, then analyses, reproduces or proposes a fix. The tickets are public. The answer key
is not: it is never committed to this repository. Design:
[`docs/rca-agent-test-application.md`](docs/rca-agent-test-application.md).

## Configure the LLM once

Copy [`.env.example`](.env.example) to `.env` at this **repository's root** and fill in the provider you use. Every
app's compose file reads that one file. `DEFAULT_LLM_PROVIDER` picks the provider for all apps;
`CHAT_LLM_PROVIDER`, `CLAIMS_LLM_PROVIDER`, `EXPENSE_LLM_PROVIDER` and `DESK_LLM_PROVIDER` override it per app.

| Provider | `DEFAULT_LLM_PROVIDER` | Needs |
|---|---|---|
| Azure OpenAI | `AZURE` | `AZURE_API_KEY`, `AZURE_ENDPOINT`, `AZURE_DEPLOYMENT` |
| Anthropic Claude | `ANTHROPIC` | `ANTHROPIC_API_KEY` (model defaults to `claude-opus-5-5`) |
| OpenAI | `OPENAI` | `OPENAI_API_KEY` |
| Ollama (local) | `OLLAMA` | Ollama running; `OLLAMA_BASE_URL`, `OLLAMA_MODEL` |
| Any OpenAI-compatible server | `OPENAI_COMPATIBLE` | `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_MODEL` |
| AWS Bedrock | `BEDROCK` | `AWS_REGION`, `BEDROCK_MODEL_ID`, AWS credentials |
| Google Gemini | `GEMINI` | `GOOGLE_API_KEY` |
| Google Vertex AI | `VERTEX` | `GOOGLE_CLOUD_PROJECT`, Application Default Credentials |
| Offline, deterministic | `SCRIPTED` | `LLM_SCRIPT` or `LLM_SCRIPT_FILE` |

Secrets live only in `.env` (git-ignored) or your environment. They are never baked into images.

## What has been verified

The factory has 45 offline tests at 99% coverage. The Bedrock requests are checked against AWS's own service
model (botocore `Stubber`), and the Gemini requests against google-genai's own types. **Azure OpenAI is
verified live** (tool call, tool result, answer, structured output). The other providers are verified
offline only until someone runs them with keys.
