---
order: 3
title: "Policies lapse 5 days after a missed installment; members were told they have 10"
labels: [rca-lab, bug]
---
**Reported by:** Grace Okafor, Retention team

**Version:** billing service 1.2.0

### What happened

We've had a run of members whose policies show as lapsed much sooner than our member handbook promises. The
handbook (and every agent script) says members have a **10-day grace period** after an installment's due date
before the policy lapses.

Example: Chen Li, policy P-5004, monthly plan. The October 20 installment wasn't paid.

```
GET /policies/P-5004
"status": "lapsed",
"standing": {"installment": 3, "due_date": "2026-10-20", "grace_ends": "2026-10-25", "lapse_date": "2026-10-26"}
```

Chen called on October 28 to pay and was refused because the policy had lapsed. By our handbook he still had until
October 30.

### Expected

`grace_ends` 2026-10-30 and `lapse_date` 2026-10-31 for this policy, and payments accepted (with the late fee) up to
October 30.

### Notes

I don't know when this started. Over the summer I don't remember any member complaining about this.
