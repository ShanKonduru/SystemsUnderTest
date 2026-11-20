# Harbor Mutual expense task agent: one image, two roles (MCP tool server, agent service); see docker-compose.yml.
# Build context is the repository root so the shared LLM factory can be installed.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EXPENSE_DB=/app/data/expense.db \
    EXPENSE_OUT=/app/data/out

WORKDIR /app

COPY common/harbor_llm /opt/harbor_llm
RUN pip install "/opt/harbor_llm[all]"

COPY expense-agent/requirements.txt .
RUN pip install -r requirements.txt

COPY expense-agent/expense_agent ./expense_agent

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8700 9700
CMD ["uvicorn", "expense_agent.service:app", "--host", "0.0.0.0", "--port", "8700"]
