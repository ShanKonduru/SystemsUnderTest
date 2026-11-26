# Billing rules

Owner: Billing product team. Applies from billing service 1.0.0. Rule ids are referenced from the code and tests.

## Premiums

**BR-01 Base rate.** Every quote starts from the base rate for the policy's state and product in
`reference/rate_tables.csv`. Harbor Mutual writes auto, home and renters in CT, MA, NJ, NY and PA. A quote for any
other state or product is refused (`unknown_territory`), never priced from a default.

**BR-02 Factors.** The base rate is multiplied by:

| Factor | Values |
|---|---|
| Coverage | basic 0.85, standard 1.00, plus 1.25 |
| Driver age (auto only) | 16-24: 1.60, 25-64: 1.00, 65-99: 1.15. An auto quote without an age, or outside 16-99, is refused. |

**BR-03 Discounts.** Discounts are percentages of the factored premium and add up:

| Discount | Rate |
|---|---|
| Multi-policy: the customer holds 2 active policies (including this one) | 5% |
| Multi-policy: the customer holds 3 active policies | 10% |
| Multi-policy (loyalty tier): the customer holds 4 or more active policies | 12% |
| Paperless billing | 2% |

So a paperless customer with three policies gets 12%, and one with six policies 14%.

**BR-04 Rounding.** Only the final annual premium is rounded, to whole cents, **half-up** (0.005 rounds up). Factors
and discounts are applied to the unrounded amount. Example: NJ auto, plus coverage, 67-year-old driver, three policies,
paperless: 1517.85 x 1.25 x 1.15 x 0.88 = 1920.08025, premium **1920.08**.

**BR-16 Premium tax.** Quotes show the state premium tax on top of the premium, and the total the member pays.
The tax is the premium times the state's rate, rounded half-up to cents; it is collected for the state and is not part
of the premium (discounts, installments and refunds work on the premium alone).

| State | CT | MA | NJ | NY | PA |
|---|---|---|---|---|---|
| Premium tax rate | 1.75% | 2.28% | 2.10% | 2.00% | 2.00% |

## Installments

**BR-05 Plans.** The annual premium is paid in 1 (annual), 2 (semiannual), 4 (quarterly) or 12 (monthly)
installments. It is split evenly in whole cents; any leftover cents go on the **first** installment, so the
installments always add up to the premium exactly.

**BR-06 Installment fees.** Each installment carries a fee: annual and semiannual none, quarterly $2.00, monthly
$3.00 (configured in `config/billing.yaml`). Fees are not part of the premium.

**BR-07 Due dates.** The first installment is due on the policy's effective date. Each later one is due 12/n months
after the previous, **on the same day of the month as the effective date**, or on the last day of the month when the
month is shorter. A monthly policy effective January 31 is due Jan 31, Feb 28 (29 in a leap year), Mar 31, Apr 30, and
so on, through Dec 31 of the same year.

**BR-08 Standing.** A policy is:

| Status | When |
|---|---|
| active | nothing is overdue (an installment due today is not overdue yet) |
| past_due | the earliest unpaid installment is past its due date, by no more than the grace period of **10 days** |
| lapsed | the grace period has ended without payment; the lapse date is the day after the grace period ends |
| cancelled | the policy was cancelled (see refund rules) |

An installment due October 20 is past due from October 21; its grace period ends October 30 (still past due); the
policy has lapsed from October 31.

## Payments

**BR-10 Billing day.** A payment counts on the calendar day it was received **in Eastern time (America/New_York)**,
wherever the member or the server is. Payment timestamps must carry a UTC offset; a timestamp without one is refused.
A payment received at 10:00 pm Eastern on the due date is on time, even though it is the next day in UTC.

**BR-11 Exact amounts and late fees.** A payment pays the earliest unpaid installment and must equal its total
(amount plus installment fee) exactly. Received after the due date but within the grace period, it must also include
a **$10.00 late fee**. Partial and over-payments are refused.

**BR-12 Lapsed policies.** A payment received after the grace period has ended is refused: the policy has lapsed and
needs reinstatement, which is handled outside this service.

**BR-19 Payment receipts.** Every accepted payment comes with a receipt: the policy, the installment, the day it
counted, whether it was late, the late fee and the amount. The receipt is signed, so the service can later confirm a
receipt it issued and refuse one that was altered or that it didn't issue.

**BR-15 Payment reminders.** A reminder is sent **7 days** before each unpaid installment's due date: an
installment due November 30 is reminded on November 23, one due March 3 on February 24, one due January 1 on
December 25 of the year before. Paid installments and cancelled policies get no reminders.

**BR-18 Reminder delivery.** Each day the reminders due that day (BR-15) are handed to the notification gateway,
which emails paperless customers and mails everyone else. A reminder gives the policy, the installment, its due date
and the amount due (premium plus installment fee). A delivery the gateway refuses is reported, and the others still
go out.

## Renewals

**BR-09 Term.** A policy runs 12 months: it renews on the same day of the month as its effective date, a year later
(February 29 renews on February 28). The term has 365 days, or 366 when it includes a February 29.

**BR-13 Renewal offer.** Only an active policy is offered a renewal. The renewal premium is quoted at the rates in
force when the offer is made, with the customer's current number of active policies.

**BR-14 Renewal notice.** The renewal notice is sent **45 days** before the renewal date. When that day is a Saturday,
a Sunday or a US federal holiday (as observed), it is sent on the business day before, so the member always gets at
least 45 days' notice. Holidays come from the `holidaycal` library (see `THIRD_PARTY.md`).

**BR-17 Renewal notice letter.** The renewal notice is a letter, dated on the notice day (BR-14), addressed to the
customer by name. It gives the policy, the renewal date and the renewal premium (BR-13), dates written out in full
("January 31, 2027") and money as dollars ("$1,411.60"). A policy that isn't offered a renewal gets no letter.
