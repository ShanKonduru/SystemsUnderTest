---
order: 38
title: "Statement says \"11st installment\""
labels: [rca-lab, bug]
---
**Reported by:** Nadia Haddad, Member Communications

**Version:** billing service 1.4.0

### What happened

Proofreading the November statements before they go out, I found this on Asha Rao's (C-1001):

```
GET /customers/C-1001/statement?month=2026-11
"lines": [{"policy_id": "P-5001", "installment": 11, "label": "11st installment", "due_date": "2026-11-30",
           "total": "120.63", "paid": false}]
```

It should say "11th installment". Chen Li's July 2027 statement (C-1003, `month=2027-07`) says "12nd installment"
too. "1st", "2nd", "3rd", "4th" and "10th" are fine.

<!-- rca-lab:comment -->
**Nadia Haddad:** IT tells me the labels come from a small library we copy into the service. They sent me this
from the library's release notes:

> **ordinals 0.4.2 (2026-11-09)**
> - Fixed: 11, 12 and 13 (and 111, 112, ...) got "st", "nd" and "rd" instead of "th" since 0.4.1.

I don't know which version we have.
