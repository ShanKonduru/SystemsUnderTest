# Member portal display rules

Owner: Member portal team. Applies to the `/portal` billing page and anything built on `portal/src`.

**PD-01 Money.** US dollar amounts show a `$`, comma thousands separators and **always two decimals**: `$1,411.60`,
`$120.67`, `$1.05`, `$0.00`. Negative amounts put the sign first: `-$12.00`. Other currencies show the amount the
same way followed by the currency code: `1,000.10 EUR`. Amounts are never rounded for display: the API already sends
exact cents.

**PD-02 Dates.** Dates show the short English month name, the day without a leading zero and the year:
`Mar 1, 2027`, `Nov 30, 2026`. API dates are calendar dates with no time or zone; the page must show the same day the
API sent, whatever the member's own timezone.

**PD-03 Due labels.** Next to an unpaid installment, relative to the service's "today":

| Days until due | Label |
|---|---|
| 0 | Due today |
| 1 | Due tomorrow |
| 2 or more | Due in N days |
| -1 | Overdue by 1 day |
| -2 or less | Overdue by N days |

Paid installments show `Paid <date>`.

**PD-04 Payment form.** The form checks everything before sending and lists every problem at once:

| Field | Rule | Message |
|---|---|---|
| Amount | dollars and cents, up to 2 decimals | Enter the amount as dollars and cents, like 120.50. |
| Amount | exactly the installment total due (BR-11) | The amount must be exactly the amount due, `<amount>`. |
| Card number | 13-19 digits (spaces and dashes allowed), passes the Luhn check | Enter a valid card number. |
| Expiry month | 1 to 12 | Enter the expiry month as 1 to 12. |
| Expiry | a card is valid through the last day of its expiry month | This card has expired. |
| ZIP code | 5 digits | Enter a 5-digit ZIP code. |
