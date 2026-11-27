---
order: 22
title: "Vermont home quotes are cheaper than renters quotes"
labels: [rca-lab, bug]
---
**Reported by:** Hannah Kowalski, Rating (2027 Vermont filing)

**Version:** billing service 1.4.0

### What happened

I was checking the first Vermont quotes against our approved 2027 filing. Auto is right, but home and renters look
backwards: a standard home policy comes out at about the price of a renters policy, and renters at the price of a
home policy.

```
POST /quotes  {"state": "VT", "product": "home", "coverage": "standard"}
-> "base_rate": "248.60", ... "premium": "248.60"

POST /quotes  {"state": "VT", "product": "renters", "coverage": "standard"}
-> "base_rate": "1089.40", ... "premium": "1089.40"
```

From the approved filing (base rates, standard coverage):

| Product | Base rate |
|---|---|
| auto | 1350.60 |
| home | 1089.40 |
| renters | 248.60 |

Agents have already sent a few home quotes to customers at the lower price.

### Expected

VT home quotes start from 1089.40 and VT renters quotes from 248.60.
