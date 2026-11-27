---
order: 33
title: "Renewal price went up after cancelling a different policy"
labels: [rca-lab, bug]
---
**Reported by:** Luis Romero, Agency Desk (for Asha Rao, C-1001)

**Version:** billing service 1.4.0

### What happened

Asha Rao cancelled her home policy P-5002 on November 10. She kept her auto policy P-5001, which renews on
January 31. Before the cancellation its renewal premium was $1,411.60; now it's $1,487.49:

```
GET /policies/P-5001/renewal           (before cancelling P-5002)
{"policy_id": "P-5001", "renewal_date": "2027-01-31", "notice_date": "2026-12-17", "eligible": true,
 "reason": null, "renewal_premium": "1411.60"}

POST /policies/P-5002/cancel  {"cancel_date": "2026-11-10"}

GET /policies/P-5001/renewal           (after)
{"policy_id": "P-5001", "renewal_date": "2027-01-31", "notice_date": "2026-12-17", "eligible": true,
 "reason": null, "renewal_premium": "1487.49"}
```

Nothing about the auto policy changed: same car, same driver, same coverage. She says cancelling one policy
shouldn't make another one more expensive, and wants the old price.

### Expected

According to her: the renewal premium stays $1,411.60.
