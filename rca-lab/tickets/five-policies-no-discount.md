---
order: 9
title: "Customer with five policies gets no multi-policy discount at all"
labels: [rca-lab, bug]
---
**Reported by:** Daniel Kim, independent agent (Kim Insurance Group, NJ)

**Version:** billing service 1.2.0

### What happened

A long-standing customer of ours holds five Harbor Mutual policies (two autos, home, renters for her daughter, and
an umbrella we write through you). I quoted her new home policy and the quote shows **no** multi-policy discount:

```
POST /quotes
{"state": "NJ", "product": "home", "coverage": "standard", "policy_count": 5, "paperless": false}
-> "discount_rate": "0.00", "multi_policy_discount": "0.00", "premium": "1098.20"
```

Her neighbour, who has four policies with you, got 12% off the same home policy last week. Out of curiosity I ran
the quote with `"policy_count": 4` and got `"discount_rate": "0.12"`, premium 966.42. With 3 policies it's 10%.

So the more policies she holds, the less she's rewarded. She's threatening to move everything.

### Expected

The loyalty discount for 4 or more policies (12%), i.e. premium 966.42.
