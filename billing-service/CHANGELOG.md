# Changelog

Releases are tagged `billing-service-v<version>`; the running version is in `GET /health`.

## 1.1.0

- Payment reminders: `GET /policies/{id}/reminders` (BR-15), lead time configurable as `reminder_days`.
- Quotes show the state premium tax and the total (BR-16).
- Refund quotes take the premium paid from the payments recorded on each installment.

## 1.0.0

- First release: quotes, installment schedules, payments with late fees, standing and lapse, renewals and renewal
  notices, free-look and pro-rata refunds, the member portal billing page.
