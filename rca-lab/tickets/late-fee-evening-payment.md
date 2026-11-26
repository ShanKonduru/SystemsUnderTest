---
order: 10
title: "Payment made at 10 pm on the due date refused unless it includes a late fee"
labels: [rca-lab, bug]
---
**Reported by:** Priya Natarajan, Member Services (escalation from Dana Park, C-1004)

**Version:** billing service 1.2.0 (production cluster `billing-prod-east`)

### What happened

Dana Park paid her P-5005 installment through our payments partner on **November 1 at 10:00 pm Eastern**, the due
date. The billing service refused it, saying it needs the $10.00 late fee:

```
POST /policies/P-5005/payments
{"amount": "960.04", "received_at": "2026-11-01T22:00:00-05:00"}

HTTP/1.1 422 Unprocessable Entity
{"error": "invalid", "detail": "installment 2 needs exactly 970.04 including the 10.00 late fee; received 960.04"}
```

She refused to pay a late fee for a payment made on the due date, so her policy is now showing past due.

From `logs/billing.log` on the production pod:

```
2026-11-02 03:00:12,481 INFO billing POST /policies/P-5005/payments -> 422 request_id=req-5c0e9a71d2b4
```

### Environment (production pod)

```
BILLING_DATA=/app/data
BILLING_TODAY=2026-11-03
BILLING_TIMEZONE=UTC
BILLING_SANDBOX_TOKEN=********
BILLING_PORT=8500
PYTHONUNBUFFERED=1
HOSTNAME=billing-prod-east-7d9f6c5b8-x2k4q
```

### Expected

Paid on the due date in Eastern time, so on time: no late fee.

<!-- rca-lab:comment -->
**Ravi Iyer, QA:** I can't reproduce this on staging. The same request against staging (same 1.2.0 image) is
accepted as on time:

```
{"policy_id": "P-5005", "installment": 2, "received_on": "2026-11-01", "late": false, "late_fee": "0.00", "amount": "960.04"}
```

Staging runs with the default settings from the image.
