# harbor-expense-agent

The expense desk of **Harbor Mutual**, a fictitious insurer: a small, event-driven back-office AI agent (no chat)
that reviews employee expense reports through MCP tools over a SQLite system of record and writes notices. It is a
**demo system under test**, made for practising AI agent testing, for example with TruVelocity's practice project
(`tv practice-project`). It is not a product. Every name and record in it is invented.

The package carries its tool server, its seed data and an LLM factory (Anthropic, OpenAI, Azure OpenAI, Ollama, any
OpenAI-compatible endpoint, AWS Bedrock, Google Gemini and Vertex AI), plus an offline rule-based stand-in for the model.

## Install and run

```
pip install "harbor-expense-agent[all]"
```

```
harbor-expense-agent
```

That starts the agent on `http://127.0.0.1:8700` and its MCP tools on `http://127.0.0.1:9700/mcp`, with the
records in `./data` (`expense.db`, `out/notices/`). Ctrl+C stops both. `harbor-expense-agent --help` lists the
options, among them `--tools-url` to put a recording proxy between the agent and its tools.

Settings come from the environment and from `.env` in the current folder:

| Setting | Purpose |
|---|---|
| `DEFAULT_LLM_PROVIDER` | `ANTHROPIC`, `AZURE`, `OPENAI`, `OLLAMA`, `OPENAI_COMPATIBLE`, `BEDROCK`, `GEMINI`, `VERTEX`, or `SCRIPTED`, plus that provider's key and model settings |
| `DEFAULT_LLM_PROVIDER=SCRIPTED` with `LLM_SCRIPT=expense_agent.policy:factory` | No LLM and no key: a deterministic rule-based policy follows the written procedures. Nothing to install beyond `pip install harbor-expense-agent` |
| `EXPENSE_SANDBOX_TOKEN` | Enables `POST /sandbox/reset` (restore the seed data) for a test harness |
| `EXPENSE_DEFECTS` | A "buggy build" for test practice: `loose_meal_limit`, `phantom_notice`, `silent_write`, `auto_reimburse`, `trace_omits_read` |

## Try one event

```
curl -X POST http://127.0.0.1:8700/events -H "X-Principal: expense-bot" -H "Content-Type: application/json" -d "{\"event\": {\"type\": \"expense.submitted\", \"report_id\": \"ER-3001\"}}"
```

The reply is the agent's own account of the episode: the route it chose, every tool call, its output and token use.

Source, procedures, tools and seed data: https://github.com/ShanKonduru/SystemsUnderTest/tree/main/expense-agent
