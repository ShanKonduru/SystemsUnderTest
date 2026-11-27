---
order: 24
title: "One returned payment and the whole policy shows as unpaid"
labels: [rca-lab, bug]
---
**Reported by:** Marcus Bell, Billing Operations (bank returns)

**Version:** billing service 1.4.0

### What happened

The bank returned Asha Rao's October payment on P-5001 (installment 10, insufficient funds), so I recorded the
return:

```
POST /policies/P-5001/installments/10/return
{"policy_id": "P-5001", "installment": 10, "returned_payment_fee": "15.00", "fee": "18.00", "amount_due": "135.63"}
```

That part looks right. But now her schedule shows **every** installment as unpaid, January to October, and every
one of them carries the $18.00 fee:

```
GET /policies/P-5001/schedule
"installments": [
  {"number": 1, "due_date": "2026-01-31", "amount": "117.67", "fee": "18.00", "total": "135.67", "paid_on": null, ...},
  {"number": 2, "due_date": "2026-02-28", "amount": "117.63", "fee": "18.00", "total": "135.63", "paid_on": null, ...},
  ...
  {"number": 10, "due_date": "2026-10-31", "amount": "117.63", "fee": "18.00", "total": "135.63", "paid_on": null, ...},
  ...
]
```

`GET /policies/P-5001` now says the policy has lapsed. Only the October payment came back; the other nine cleared
months ago.

### Expected

Only installment 10 is unpaid again, with the $15.00 returned-payment fee added to its fee. Installments 1-9 keep
their payments and their $3.00 fee.
