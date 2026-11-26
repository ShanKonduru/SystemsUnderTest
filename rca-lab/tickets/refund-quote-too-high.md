---
order: 7
title: "Cancellation refund for a monthly policy is $30 higher than finance's figure"
labels: [rca-lab, bug]
---
**Reported by:** Olivia Chen, Finance (refunds reconciliation)

**Version:** billing service 1.2.0

### What happened

Asha Rao asked what she'd get back if she cancelled her monthly auto policy P-5001 today (Nov 3). The refund quote:

```
GET /policies/P-5001/refund-quote?cancel_date=2026-11-03
{"policy_id": "P-5001", "kind": "pro_rata", "paid": "1206.34", "earned": "1071.27", "fee": "25.00",
 "refund": "110.07", "days_in_force": 277, "term_days": 365}
```

Finance's reconciliation has her premium paid so far at **1176.34** (installment 1 = 117.67, installments 2-10 =
117.63 each), so the refund should be 1176.34 - 1071.27 - 25.00 = **80.07**. The service says she paid 1206.34
and quotes 110.07, exactly $30.00 more.

We're holding all refunds on monthly-plan policies until this is understood. Annual policies reconcile fine
(P-5003 quoted 1350.40 and that matched).

### Expected

`paid` 1176.34, `refund` 80.07.
