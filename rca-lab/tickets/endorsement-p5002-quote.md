---
order: 29
title: "Asha Rao's downgrade to standard coverage saves less than it should"
labels: [rca-lab, bug]
---
**Reported by:** Luis Romero, Agency Desk (for Asha Rao, C-1001)

**Version:** billing service 1.4.0

### What happened

Asha Rao wants to drop her home policy P-5002 from plus to standard coverage for the rest of the term. The
endorsement quote:

```
GET /policies/P-5002/endorsement-quote?coverage=standard
{"policy_id": "P-5002", "on": "2026-11-03", "coverage_from": "plus", "coverage_to": "standard",
 "old_premium": "1276.66", "new_premium": "1043.29", "days_remaining": 149, "term_days": 365,
 "adjustment": "-95.27"}
```

A new NJ home quote at standard coverage for her (two policies, paperless) is $1,021.33, so the endorsement's new
premium is about $22 a year too high and her refund for the rest of the term is too small.

### Expected

`"new_premium": "1021.33"`, and the adjustment worked out from it.
