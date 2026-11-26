# Harbor Mutual billing service: the API, the member portal page and a SQLite store, in one container. No LLM.
# Build context is the repository root, like the other apps. The same Dockerfile builds any billing-service tag
# (see README.md, "Run it in Docker").

# The portal page: TypeScript with its types stripped by Node, no npm packages.
FROM node:24-slim AS portal
WORKDIR /src
COPY billing-service/portal ./portal
RUN node portal/build.mjs

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BILLING_DATA=/app/data

WORKDIR /app

COPY billing-service/requirements.txt .
RUN pip install -r requirements.txt

COPY billing-service/billing ./billing
COPY billing-service/config ./config
COPY billing-service/reference ./reference
COPY --from=portal /src/portal/dist ./portal/dist

# Non-root user; billing.db and logs/billing.log live in /app/data.
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
USER app
VOLUME ["/app/data"]

EXPOSE 8500
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8500/health', timeout=2)"

CMD ["uvicorn", "billing.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8500"]
