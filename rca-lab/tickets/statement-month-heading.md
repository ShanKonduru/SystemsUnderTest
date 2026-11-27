---
order: 30
title: "Statement page shows the wrong month in its heading"
labels: [rca-lab, bug]
---
**Reported by:** Sofia Alvarez, Member Portal Support

**Version:** billing service 1.4.0

### What happened

On the new statement page, Asha Rao's November statement is headed with the wrong month:

`/portal/statement.html?customer=C-1001&month=2026-11`

> **Statement for December 2026**
>
> | Policy | Installment | Due | Amount | Status |
> |---|---|---|---|---|
> | P-5001 | 11st installment | Nov 30, 2026 | $120.63 | Unpaid |

The line underneath is her November installment, due November 30, so it's the November statement with a December
heading. I checked a couple of other months and the heading is always one month later than the statement. Members
have called asking whether they already owe December's payment.

(The "11st" is already reported separately.)

### Expected

`Statement for November 2026` for `month=2026-11`.
