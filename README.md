# Systems under test (SUT)

Demo applications that TruAssure tests. They belong to the fictitious insurer **Harbor Mutual**; no real
customer or company data is used anywhere. Each app has its own `docker-compose.yml` for Docker Desktop.

| App | Folder | What it is | Status |
|---|---|---|---|
| LLM factory | [`common/harbor_llm`](common/harbor_llm) | One interface over every LLM provider, chosen by `DEFAULT_LLM_PROVIDER`; bounded timeouts (`LLM_TIMEOUT`, `LLM_MAX_RETRIES`) | ready (99% coverage, 45 tests) |
| Chat assistant | [`chat-assistant/`](chat-assistant) | Customer chat: web UI, MCP tool server, SQLite system of record | ready (99% coverage, verified live) |
| Claims task agent | [`claims-agent/`](claims-agent) | Event-driven back-office agent (no chat): MCP tools over HTTP, SQLite, letters folder | ready (98.7% coverage, verified live) |
| Multi-agent desk | [`multi-agent-desk/`](multi-agent-desk) | Triage agent handing off to billing and claims agents over HTTP, signed identity carried along, fault switch | ready (98.9% coverage, verified live) |

## Configure the LLM once

Copy [`../.env.example`](../.env.example) to `.env` at the **repo root** and fill in the provider you use. Every
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
