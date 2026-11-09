# Systems under test (SUT)

Demo applications that TruAssure tests. They belong to the fictitious insurer **Harbor Mutual**; no real
customer or company data is used anywhere. Each app has its own `docker-compose.yml` for Docker Desktop.

These apps used to live in TruAssure's `SUT/` folder (their history came along). TruAssure never imports their code:
it tests them black-box, over HTTP, like any client's agent.

- **Images:** on every push to `main` that passes CI, this repository publishes `ghcr.io/shankonduru/harbor-claims-agent`,
  `harbor-member-desk`, `harbor-chat-assistant` and `harbor-automation-lab`, tagged `sha-<commit>` and `latest`. TruAssure's offline pilot tests
  pin a `sha-` tag and run those images with a scripted model (no LLM).
- **Live demos:** TruAssure's `scripts/truassure-ops.*` deploy these apps from a clone of this repository next to
  TruAssure's (or wherever `TA_SUT_DIR` points), with TruAssure's `.env`. The observed deployments
  (`docker-compose.observed.yml`) put TruAssure's recording proxy in front of an app's tools; the scripts build that
  image (`truassure/proxy:dev`) from TruAssure's source first.

| App | What it is | Local URL |
|---|---|---|
| [`chat-assistant`](chat-assistant) | Customer chat assistant (login, MCP tools, LLM) | http://127.0.0.1:8000 |
| [`claims-agent`](claims-agent) | Event-driven back-office claims agent (no UI) | http://127.0.0.1:8100 |
| [`multi-agent-desk`](multi-agent-desk) | Triage, billing and claims agents with identity propagation | http://127.0.0.1:8300 |
| [`automation-lab`](automation-lab) | Web UI for test-script generation, execution and self-healing (no login, no LLM) | http://127.0.0.1:8400 |

## Configure the LLM once

Copy [`.env.example`](.env.example) to `.env` at this **repository's root** and fill in the provider you use. Every
app's compose file reads that one file. `DEFAULT_LLM_PROVIDER` picks the provider for all apps;
`CHAT_LLM_PROVIDER`, `CLAIMS_LLM_PROVIDER` and `DESK_LLM_PROVIDER` override it per app.

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
