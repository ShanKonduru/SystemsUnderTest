---
order: 12
title: "Reminder for the January 1 installment is scheduled for December 2027"
labels: [rca-lab, bug]
---
**Reported by:** Marcus Bell, Billing Operations

**Version:** billing service 1.2.0

### What happened

Reviewing the reminder queue for December, I noticed Asha Rao's quarterly home policy P-5002 has no reminder in
December 2026 for its January 1 installment. Instead the reminder sits a year out:

```
GET /policies/P-5002/reminders
{"policy_id": "P-5002", "reminders": [
  {"installment": 4, "due_date": "2027-01-01", "remind_on": "2027-12-25", "status": "scheduled"}
]}
```

So she'll get no reminder before her January payment, and a reminder a year after it's due.

Reminders for other months look right, e.g. P-5001: installment 11 due 2026-11-30 reminded 2026-11-23, installment
12 due 2026-12-31 reminded 2026-12-24.

### Expected

`"remind_on": "2026-12-25"`, a week before January 1.
