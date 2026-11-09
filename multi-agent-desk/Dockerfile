# Harbor Mutual member desk: one image, four roles (tools, triage, billing, claims); see docker-compose.yml.
# Build context is the repository root so the shared LLM factory can be installed.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DESK_DATA=/app/data

WORKDIR /app

COPY common/harbor_llm /opt/harbor_llm
RUN pip install "/opt/harbor_llm[all]"

COPY multi-agent-desk/requirements.txt .
RUN pip install -r requirements.txt

COPY multi-agent-desk/desk ./desk

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8300 8301 8302 9300
CMD ["uvicorn", "desk.service:triage_app", "--factory", "--host", "0.0.0.0", "--port", "8300"]
