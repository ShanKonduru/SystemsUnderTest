---
order: 20
title: "Reinstatement quotes only charge a $5 fee"
labels: [rca-lab, bug]
---
**Reported by:** Grace Liu, Billing Operations (reinstatement desk)

**Version:** billing service 1.4.0

### What happened

Since reinstatement went live, the amount we quote members includes a reinstatement fee of $5.00:

```
GET /policies/P-5004/reinstatement
{"policy_id": "P-5004", "lapse_date": "2026-10-26", "last_day": "2026-11-25", "installments": [3],
 "reinstatement_fee": "5.00", "amount_due": "101.95"}
```

Our reinstatement fee is $50.00. That is what the member letters and the website say, and what the billing rules
we signed off say. Three members have already reinstated at the lower amount.

### Expected

`"reinstatement_fee": "50.00"`, so `"amount_due": "146.95"` for P-5004 today.
