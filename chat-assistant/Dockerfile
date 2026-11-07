# Harbor Mutual chat assistant: web UI + MCP tool server (stdio subprocess) + SQLite, in one container.
# Build context is SUT/ (see docker-compose.yml) so the shared LLM factory can be installed.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    COPILOT_DB=/app/data/insurance.db \
    COPILOT_MCP_TRANSPORT=stdio

WORKDIR /app

# Shared LLM factory with every provider SDK, so DEFAULT_LLM_PROVIDER can be switched without a rebuild.
COPY common/harbor_llm /opt/harbor_llm
RUN pip install "/opt/harbor_llm[all]"

COPY chat-assistant/requirements.txt .
RUN pip install -r requirements.txt

COPY chat-assistant/insurance_copilot ./insurance_copilot

# Non-root user; the database lives in a volume at /app/data.
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
USER app
VOLUME ["/app/data"]

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"

CMD ["uvicorn", "insurance_copilot.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
