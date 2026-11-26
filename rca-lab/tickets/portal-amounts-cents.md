---
order: 1
title: "Member portal shows $960.40 for an installment billed at $960.04"
labels: [rca-lab, bug]
---
**Reported by:** Priya Natarajan, Member Services (call from member C-1004, Dana Park)

**Version:** billing service 1.2.0 (`/health` on production)

### What happened

Dana Park called because her billing page doesn't match her paper statement. Her statement for policy P-5005 says
each semiannual installment is **$960.04**. The portal page (`/portal?policy=P-5005`) shows:

```
Installments
#  Due          Amount    Status
1  May 1, 2026  $960.40   Paid May 1, 2026
2  Nov 1, 2026  $960.40   Overdue by 2 days

Next payment  $970.40 on Nov 1, 2026 (includes a $10.00 late fee)
```

She tried to pay the $970.40 the page shows and the form told her: "The amount must be exactly the amount due,
970.04." So the page itself knows the amount is 970.04 but displays 970.40.

### Expected

The page shows the same amounts as the statement and the API: $960.04 per installment, $970.04 with the late fee.

### Checked

`GET /policies/P-5005/schedule` returns `"total": "960.04"` for both installments, so the API is right.
Other members' pages look fine to me (P-5001 shows $120.63 correctly).
