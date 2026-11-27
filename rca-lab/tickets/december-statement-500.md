---
order: 27
title: "December statements fail with an internal error"
labels: [rca-lab, bug]
---
**Reported by:** Marcus Bell, Billing Operations (statement print run)

**Version:** billing service 1.4.0

### What happened

I'm setting up the December statement print run. Every December statement fails; November and January are fine.

```
GET /customers/C-1003/statement?month=2026-12
HTTP/1.1 500 Internal Server Error
{"error": "internal", "request_id": "req-7f3c21a9d0e4"}

GET /customers/C-1003/statement?month=2026-11
HTTP/1.1 200 OK
{"customer_id": "C-1003", "name": "Chen Li", "month": "2026-11", "lines": [...], "amount_due": "96.95"}
```

The log entry for that request id:

```
2026-11-27 07:42:13,421 ERROR billing unhandled error on GET /customers/C-1003/statement request_id=req-7f3c21a9d0e4
2026-11-27 07:42:13,437 INFO billing GET /customers/C-1003/statement -> 500 request_id=req-7f3c21a9d0e4
```

It's the same for every customer I tried. The December run goes to the printer on December 1.

### Expected

The December statement, like any other month.
