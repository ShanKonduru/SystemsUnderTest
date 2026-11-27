---
order: 28
title: "Schedule preview errors out for the new bimonthly plan"
labels: [rca-lab, bug]
---
**Reported by:** Ella Brooks, Sales Enablement (quote screen)

**Version:** billing service 1.4.0

### What happened

The quote screen shows the payment schedule for each plan before the customer picks one. Since 1.4.0 added the
bimonthly plan, the preview fails for it, and only for it:

```
POST /schedules/preview
{"premium": "1200.00", "plan": "bimonthly", "effective_date": "2027-01-15"}

HTTP/1.1 500 Internal Server Error
{"error": "internal", "request_id": "req-f5cef27879ce"}
```

The same request with `"plan": "quarterly"` or `"plan": "monthly"` works. A made-up plan (`"weekly"`) gives a
proper 422, not a 500.

Sales was told bimonthly is ready to sell from December 1.

### Expected

Six installments of $200.00, every two months from January 15, each with the $2.50 bimonthly installment fee.
