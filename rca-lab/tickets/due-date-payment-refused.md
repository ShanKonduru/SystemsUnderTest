---
order: 16
title: "On the due date the portal asks for a late fee, then the payment is refused"
labels: [rca-lab, bug]
---
**Reported by:** Priya Natarajan, Member Services (call from Asha Rao, C-1001)

**Version:** billing service 1.2.0. This worked last month, on 1.1.0.

### What happened

Asha Rao tried to pay her November installment for P-5001 **on its due date, November 30**. The portal said the
amount due was $130.63, "includes a $10.00 late fee". She thought that was wrong (it's not late on the due date)
but paid $130.63 anyway. The payment was refused:

```
GET /policies/P-5001         (on 2026-11-30)
"next_payment": {"installment": 11, "due_date": "2026-11-30", "late_fee": "10.00", "amount_due": "130.63"}

POST /policies/P-5001/payments
{"amount": "130.63", "received_at": "2026-11-30T15:00:00-05:00"}
-> 422 {"error": "invalid", "detail": "installment 11 needs exactly 120.63; received 130.63"}
```

So the policy page and the payment endpoint disagree about whether a payment on the due date is late. She had to call
us to pay $120.63 by phone.

On October 31, under 1.1.0, she paid installment 10 on its due date through the portal with no problem: the page
asked for $120.63 and the payment went through.

### Expected

On the due date the portal asks for the installment total without a late fee ($120.63), and that payment is
accepted.

### To reproduce

Run with `BILLING_TODAY=2026-11-30` (the service's "today") and a fresh database.
