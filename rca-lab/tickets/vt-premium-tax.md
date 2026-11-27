---
order: 34
title: "Vermont premium tax on quotes is far too low"
labels: [rca-lab, bug]
---
**Reported by:** Owen Fitzgerald, Finance (state tax filings)

**Version:** billing service 1.4.0

### What happened

Our first Vermont quotes show a premium tax of a few dollars. Vermont's premium tax rate is 2.40%, so on a quote of
about $1,350 the tax should be over $30:

```
POST /quotes  {"state": "VT", "product": "auto", "coverage": "standard", "driver_age": 40}
-> "premium": "1350.60", "premium_tax": "3.24", "total": "1353.84"
```

2.40% of 1350.60 is 32.41. Quotes for the other states look right to me, for example NJ renters:
`"premium": "198.65", "premium_tax": "4.17"` (2.10%).

If this goes out on policies we'll under-collect the state's tax and have to make it up ourselves.

### Expected

`"premium_tax": "32.41"` and `"total": "1383.01"` for that quote.
