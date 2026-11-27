---
order: 35
title: "Statement page shows Dana Park owing $960.40 instead of $960.04"
labels: [rca-lab, bug]
---
**Reported by:** Priya Natarajan, Member Services (call from Dana Park, C-1004)

**Version:** billing service 1.4.0

### What happened

Dana Park's November statement on the portal says she owes $960.40:

`/portal/statement.html?customer=C-1004&month=2026-11`

> | Policy | Installment | Due | Amount | Status |
> |---|---|---|---|---|
> | P-5005 | 2nd installment | Nov 1, 2026 | $960.40 | Unpaid |
>
> Amount due: **$960.40**

The API gives the right amount:

```
GET /customers/C-1004/statement?month=2026-11
"lines": [{"policy_id": "P-5005", "installment": 2, "label": "2nd installment", "due_date": "2026-11-01",
           "total": "960.04", "paid": false}], "amount_due": "960.04"
```

Other customers' statements look fine, for example $120.63 and $96.95.

### Expected

`$960.04`, as the API says.
