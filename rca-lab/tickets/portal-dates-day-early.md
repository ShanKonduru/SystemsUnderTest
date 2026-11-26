---
order: 15
title: "Portal shows my due dates and renewal date one day early"
labels: [rca-lab, bug]
---
**Reported by:** Dana Park, member C-1004 (via the website feedback form, forwarded by Member Services)

**Version:** billing service 1.2.0

### What the member wrote

"My statement says my auto installments are due on the 1st of May and November, and the policy renews May 1. On
your website it says April 30 and October 31, and renews April 30, 2027. Which is it? I don't want to be charged a
late fee because your website gave me the wrong date."

### What we see

Her page, `/portal?policy=P-5005`, as she sees it (she's in New Jersey; Chrome on Windows):

```
Renews on      Apr 30, 2027
Next payment   ... on Oct 31, 2026
Installments
1  Apr 30, 2026  ...  Paid Apr 30, 2026
2  Oct 31, 2026  ...  Overdue by 2 days
```

The API has `"effective_date": "2026-05-01"`, `"renewal_date": "2027-05-01"`, and installments due `2026-05-01` and
`2026-11-01`.

Our support desk is in Bangalore, and when they open the same page they see **May 1** and **Nov 1**, so at first
they told her the site was right.

### Expected

The page shows the dates the API sends: May 1, 2026, Nov 1, 2026, renews May 1, 2027.
