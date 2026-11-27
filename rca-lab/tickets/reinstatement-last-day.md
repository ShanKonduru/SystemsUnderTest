---
order: 36
title: "Reinstatement refused on the last day the error message says is allowed"
labels: [rca-lab, bug]
---
**Reported by:** Priya Natarajan, Member Services (call from Chen Li, C-1003)

**Version:** billing service 1.4.0

### What happened

Chen Li's auto policy P-5004 lapsed in October. He called on **November 25** to reinstate it, and the service
refused:

```
GET /policies/P-5004/reinstatement          (on 2026-11-25)
HTTP/1.1 409 Conflict
{"error": "conflict",
 "detail": "the policy lapsed on 2026-10-26; it could be reinstated until 2026-11-25"}
```

So it says he could reinstate until November 25, and refuses on November 25. The day before, the same request
gave the terms without a problem:

```
GET /policies/P-5004/reinstatement          (on 2026-11-24)
{"policy_id": "P-5004", "lapse_date": "2026-10-26", "last_day": "2026-11-25", "installments": [3, 4], ...}
```

Our reinstatement letter tells members they have 30 days after the lapse date, the last day included. He now has
to buy a new policy.

### Expected

A reinstatement on the last day (`last_day`) is accepted; it is refused from the day after.

### To reproduce

Run with `BILLING_TODAY=2026-11-25` and a fresh database.
