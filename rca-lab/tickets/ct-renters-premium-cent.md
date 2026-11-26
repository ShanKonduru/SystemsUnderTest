---
order: 4
title: "Connecticut renters quote is one cent below the rating worksheet"
labels: [rca-lab, bug]
---
**Reported by:** Hannah Weiss, Rating & Pricing (quarterly rate audit)

**Version:** billing service 1.2.0

### What happened

In the quarterly audit we re-price a sample of quotes by hand from the rate table. One doesn't match:

```
POST /quotes
{"state": "CT", "product": "renters", "coverage": "standard", "policy_count": 2, "paperless": false}

{"base_rate": "214.30", "coverage_factor": "1.00", "age_factor": "1.00", "subtotal": "214.30",
 "discount_rate": "0.05", "multi_policy_discount": "10.72", "paperless_discount": "0.00",
 "discount": "10.72", "premium": "203.58", "premium_tax": "3.56", "total": "207.14", "currency": "USD"}
```

Our worksheet: 214.30 x 0.95 = 203.585, which rounds to **203.59**. The service quotes **203.58**.

Note `discount` says 10.72 while 214.30 - 203.58 = 10.72, so the response is consistent with itself; it's the
premium that's a cent low.

### Expected

`premium` 203.59 (and the tax and total that follow from it).

### Scope

The other 23 quotes in the sample matched to the cent.
