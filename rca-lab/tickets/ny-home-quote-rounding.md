---
order: 11
title: "New York home quote: premium off by a penny vs. our filed rate manual"
labels: [rca-lab, bug]
---
**Reported by:** Leah Abramowitz, NY Compliance

**Version:** billing service 1.2.0

### What happened

The NY regulator's spot check asked us to show how a basic-coverage home premium is derived. Following our filed rate
manual by hand:

- base rate NY home 1204.90
- basic coverage factor 0.85
- 1204.90 x 0.85 = 1024.165, rounded to the cent = **1024.17**

The service quotes:

```
POST /quotes
{"state": "NY", "product": "home", "coverage": "basic"}
-> "subtotal": "1024.17", "premium": "1024.16"
```

The `subtotal` line agrees with the manual (1024.17) but the `premium` doesn't (1024.16), even though there's no
discount on this quote. Odd that the same number is rounded two ways in one response.

We have to answer the regulator by the 15th.

### Expected

`premium` 1024.17.
