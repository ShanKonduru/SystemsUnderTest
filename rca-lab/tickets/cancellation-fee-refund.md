---
order: 13
title: "Member charged $25 to cancel; refund should be the full unearned premium"
labels: [rca-lab, bug]
---
**Reported by:** Priya Natarajan, Member Services (complaint from Ben Ortiz, C-1002)

**Version:** billing service 1.2.0

### What happened

Ben Ortiz cancelled his annual auto policy P-5003 effective November 3. He'd paid the full year (2251.21) in June.
The cancellation gave him:

```
POST /policies/P-5003/cancel
{"cancel_date": "2026-11-03"}
-> {"policy_id": "P-5003", "kind": "pro_rata", "paid": "2251.21", "earned": "875.81", "fee": "25.00",
    "refund": "1350.40", "days_in_force": 142, "term_days": 365, "status": "cancelled"}
```

Ben's words: "I paid for a year, I used 142 days, so you owe me 2251.21 - 875.81 = 1375.40. Where did the other $25
go? Nobody told me there's a charge for cancelling. Refund the $25."

He's cancelled more than a week after the policy started, so he's outside the cooling-off period.

### Expected (per the member)

Refund 1375.40.
