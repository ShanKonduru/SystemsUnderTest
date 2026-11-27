---
order: 25
title: "Coverage-change quote for P-5006 is higher than a new quote for the same coverage"
labels: [rca-lab, bug]
---
**Reported by:** Priya Natarajan, Member Services (call from Dana Park, C-1004)

**Version:** billing service 1.4.0

### What happened

Dana Park asked what it would cost to move her home policy P-5006 from standard to plus coverage from today
(November 3). The endorsement quote gives a new annual premium of $1,235.48:

```
GET /policies/P-5006/endorsement-quote?coverage=plus
{"policy_id": "P-5006", "on": "2026-11-03", "coverage_from": "standard", "coverage_to": "plus",
 "old_premium": "966.42", "new_premium": "1235.48", "days_remaining": 179, "term_days": 365,
 "adjustment": "131.95"}
```

Out of curiosity I quoted the same thing as a new policy: NJ home, plus coverage, three policies (she has three
with us), paperless (she is). That comes out cheaper:

```
POST /quotes
{"state": "NJ", "product": "home", "coverage": "plus", "policy_count": 3, "paperless": true}
-> "premium": "1208.02"
```

She says she'd be better off cancelling and buying a new policy, which can't be right.

### Expected

The endorsement's new premium is what a new quote for the same coverage would be today: $1,208.02.
