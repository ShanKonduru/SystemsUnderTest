---
order: 26
title: "Statement charges $3 more than the installment"
labels: [rca-lab, bug]
---
**Reported by:** Priya Natarajan, Member Services (complaint from Asha Rao, C-1001)

**Version:** billing service 1.4.0

### What happened

Asha Rao says her November statement overcharges her. Her monthly installment on P-5001 is $117.63, but the
statement says she owes $120.63:

```
GET /policies/P-5001/schedule
{"number": 11, "due_date": "2026-11-30", "amount": "117.63", "fee": "3.00", "total": "120.63", "paid_on": null, ...}

GET /customers/C-1001/statement?month=2026-11
"lines": [{"policy_id": "P-5001", "installment": 11, "label": "11st installment", "due_date": "2026-11-30",
           "total": "120.63", "paid": false}], "amount_due": "120.63"
```

She wants the extra $3.00 taken off the statement, and asks whether we've been overcharging every monthly customer.

### Expected

According to her: the statement asks for the installment, $117.63.
