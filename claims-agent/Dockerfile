# Harbor Mutual claims task agent: one image, two roles (MCP tool server, agent service); see docker-compose.yml.
# Build context is SUT/ so the shared LLM factory can be installed.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CLAIMS_DB=/app/data/claims.db \
    CLAIMS_LETTERS=/app/data/letters

WORKDIR /app

COPY common/harbor_llm /opt/harbor_llm
RUN pip install "/opt/harbor_llm[all]"

COPY claims-agent/requirements.txt .
RUN pip install -r requirements.txt

COPY claims-agent/claims_agent ./claims_agent

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8100 9100
CMD ["uvicorn", "claims_agent.service:app", "--host", "0.0.0.0", "--port", "8100"]
