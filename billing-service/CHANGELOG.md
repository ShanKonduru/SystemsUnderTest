# Changelog

Releases are tagged `billing-service-v<version>`; the running version is in `GET /health`.

## 1.4.0

- Vermont: 2027 rates for auto, home and renters, and the VT premium tax (BR-01, BR-16).
- Endorsements: `GET /policies/{id}/endorsement-quote?coverage=&on=` quotes a coverage change mid-term (BR-20).
- Returned payments: `POST /policies/{id}/installments/{n}/return`, with the returned-payment fee (BR-21).
- Reinstatement: `GET /policies/{id}/reinstatement` and `POST /policies/{id}/reinstate` for a policy that lapsed
  within the last 30 days (BR-22).
- Monthly statements: `GET /customers/{id}/statement?month=`, with installment labels ("4th installment"), and the
  portal's statement page `/portal/statement.html` (BR-23, PD-05). Ordinals vendored at 0.4.1.
- A bimonthly plan (BR-05, BR-06) and `POST /schedules/preview`.
- Portal: the payment form asks for the card security code (PD-04).
- `config/billing.staging.yaml` for the staging cluster.

## 1.3.0

- Renewal notice letters: `GET /policies/{id}/renewal-notice` (BR-17), rendered from HTML templates.
- Reminder delivery: `POST /reminders/send` hands the day's reminders to the notification gateway (BR-18),
  configured with `BILLING_NOTIFY_URL`.
- Payment receipts: every payment answers with a signed `receipt`; `GET /receipts/{receipt}` checks one (BR-19).
  The signing key is `BILLING_RECEIPT_KEY`.

## 1.2.0

- Quotes itemise the multi-policy and paperless discounts.
- Multi-policy discount: 12% loyalty tier from four active policies (BR-03).
- 2027 Massachusetts rates.
- `GET /calendar/holidays` for the ops team; holidaycal updated to 1.3.0.
- Portal: dates formatted with `Intl.DateTimeFormat`; a `splitCents` helper for money display.
- The policy's `next_payment` late fee comes from `payments.late_fee_on`.

## 1.1.0

- Payment reminders: `GET /policies/{id}/reminders` (BR-15), lead time configurable as `reminder_days`.
- Quotes show the state premium tax and the total (BR-16).
- Refund quotes take the premium paid from the payments recorded on each installment.

## 1.0.0

- First release: quotes, installment schedules, payments with late fees, standing and lapse, renewals and renewal
  notices, free-look and pro-rata refunds, the member portal billing page.
