---
order: 2
title: "POST /quotes returns 500 for every Massachusetts quote"
labels: [rca-lab, bug]
---
**Reported by:** Tomás Herrera, Agency Support

**Version:** billing service 1.2.0

### What happened

Our Massachusetts agents can't get any quote since this week. Every request fails with a 500, for home, auto and
renters alike. Other states work.

```
POST /quotes
{"state": "MA", "product": "home"}

HTTP/1.1 500 Internal Server Error
x-request-id: req-39c2b63e88e0
{"error": "internal", "request_id": "req-39c2b63e88e0"}
```

### From the service log (`logs/billing.log`) for that request id

```
2026-11-26 01:46:33,118 ERROR billing unhandled error on POST /quotes request_id=req-39c2b63e88e0
Traceback (most recent call last):
  File "/app/billing/api.py", line 79, in request_log
    response: Response = await call_next(request)
                         ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/starlette/middleware/base.py", line 179, in call_next
    raise app_exc from app_exc.__cause__ or app_exc.__context__
  File "/usr/local/lib/python3.12/site-packages/starlette/middleware/base.py", line 149, in coro
    await self.app(scope, receive_or_disconnect, send_no_error)
  [... starlette / fastapi / anyio frames ...]
  File "/usr/local/lib/python3.12/site-packages/fastapi/telemetry/_api.py", line 244, in _run_sync_endpoint
    return function(**arguments)
           ^^^^^^^^^^^^^^^^^^^^^
  File "/app/billing/api.py", line 98, in quote
    tax = premium_tax(body.state, q.premium)
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/billing/taxes.py", line 16, in premium_tax
    return to_money(premium * PREMIUM_TAX_RATES[state.upper()])
                              ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^
KeyError: 'MA'
2026-11-26 01:46:33,136 INFO billing POST /quotes -> 500 request_id=req-39c2b63e88e0
```

### Expected

A quote with the premium, the Massachusetts premium tax and the total, like the other states.
