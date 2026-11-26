# Third-party code

Copied into `billing/_vendor/` at a pinned version, unmodified. To change it, update the copy from upstream and
record the new version here; don't patch it in place.

| Package | Version | Latest upstream | Upstream | License | Used by |
|---|---|---|---|---|---|
| holidaycal | 1.3.0 | 1.3.1 | https://example.org/tidewater/holidaycal (Tidewater Software Cooperative) | MIT | `billing/renewals.py`: renewal notices go out on business days; `billing/api.py`: `GET /calendar/holidays` |
