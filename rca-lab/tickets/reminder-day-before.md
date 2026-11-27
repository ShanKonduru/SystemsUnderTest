---
order: 39
title: "Payment reminders arrive the day before the due date"
labels: [rca-lab, bug]
---
**Reported by:** Priya Natarajan, Member Services (several calls)

**Version:** billing service 1.4.0 (production cluster `billing-prod-east`)

### What happened

Since the 1.4.0 upgrade, members say their payment reminder arrives the day before the payment is due, which
leaves them no time to pay. It used to come a week ahead. For Asha Rao (C-1001):

```
GET /policies/P-5001/reminders        (production)
{"policy_id": "P-5001", "reminders": [
  {"installment": 11, "due_date": "2026-11-30", "remind_on": "2026-11-29", "status": "scheduled"},
  {"installment": 12, "due_date": "2026-12-31", "remind_on": "2026-12-30", "status": "scheduled"}]}
```

When I run the same request on my laptop against 1.4.0, I get November 23 and December 24, which is what I'd
expect. The platform team says production runs the same 1.4.0 image as the release.

### Environment (production pod)

```
BILLING_DATA=/app/data
BILLING_TODAY=2026-11-03
BILLING_CONFIG=config/billing.staging.yaml
BILLING_NOTIFY_URL=https://notify.internal.harbormutual.example/v1/messages
BILLING_SANDBOX_TOKEN=********
BILLING_PORT=8500
PYTHONUNBUFFERED=1
HOSTNAME=billing-prod-east-6c8d9f7b4-q7m2n
```

### Expected

Reminders go out a week before the due date: November 23 for the November 30 installment.
