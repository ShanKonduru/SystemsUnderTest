# Harbor Mutual Automation Lab: the page, its API and a SQLite store, in one container. No LLM.
# Build context is the repository root, like the other apps (and CI's image publishing loop).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LAB_DATA=/app/data

WORKDIR /app

COPY automation-lab/requirements.txt .
RUN pip install -r requirements.txt

COPY automation-lab/automation_lab ./automation_lab

# Non-root user; lab.db and uploads live in /app/data.
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
USER app
VOLUME ["/app/data"]

EXPOSE 8400
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8400/healthz', timeout=2)"

CMD ["uvicorn", "automation_lab.app:app", "--host", "0.0.0.0", "--port", "8400"]
