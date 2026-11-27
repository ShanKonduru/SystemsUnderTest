---
order: 31
title: "Partner quote requests refused since the last release: \"doesn't write policies in 'nj'\""
labels: [rca-lab, bug]
---
**Reported by:** Raj Mehta, Partner Integrations

**Version:** billing service 1.4.0. This worked on 1.3.0.

### What happened

Since the 1.4.0 release, every quote request from our comparison-site partner is refused. They send the state in
lower case, which has always worked:

```
POST /quotes
{"state": "nj", "product": "renters", "coverage": "standard"}

HTTP/1.1 422 Unprocessable Entity
{"error": "unknown_territory",
 "detail": "Harbor Mutual doesn't write policies in 'nj'; it writes in CT, MA, NJ, NY, PA, VT"}
```

The same request with `"state": "NJ"` is quoted. Their integration hasn't changed in a year, and on 1.3.0 the
lower-case request got the same quote as the upper-case one.

We've lost two days of partner quotes so far.

### Expected

`"nj"` is quoted exactly like `"NJ"`.
