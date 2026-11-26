---
order: 14
title: "Pennsylvania home quotes priced from the wrong base rate"
labels: [rca-lab, bug]
---
**Reported by:** Hannah Weiss, Rating & Pricing

**Version:** billing service 1.2.0

### What happened

Pennsylvania home quotes are coming out about 3.7% cheaper than they should:

```
POST /quotes
{"state": "PA", "product": "home", "coverage": "standard"}
-> "base_rate": "926.35", "premium": "926.35"
```

Our filed and approved PA home base rate is **962.35** and hasn't changed this year. The only rate change this
season was the 2027 Massachusetts filing; nothing for Pennsylvania was filed.

PA auto (1127.45) and PA renters (176.80) quote at their filed rates.

### Expected

`"base_rate": "962.35"` for PA home.
