# Refund rules

Owner: Policy servicing. Applies from billing service 1.0.0.

**RF-01 Cancellation date.** A policy can be cancelled on any day from its effective date up to the day before its
renewal date. A cancelled policy can't be cancelled again or take payments.

**RF-02 Free-look period.** A member who cancels within **10 days** of the effective date, counting the effective date
as day 1, gets back every premium dollar paid, with no fee. A policy effective October 28 can be cancelled under the
free-look period up to and including November 6.

**RF-03 Pro-rata refund.** After the free-look period, Harbor Mutual keeps the premium earned for the days the policy
was in force:

    earned = annual premium x days in force / days in the term
    refund = premium paid - earned - cancellation fee, and never less than zero

Days in force count from the effective date **to the cancellation date, both included**. Days in the term are 365, or
366 when the term includes a February 29 (BR-09). `earned` is rounded half-up to cents.

Example: annual premium 2251.21, all paid, effective June 15, cancelled November 3: 142 days in force,
earned 2251.21 x 142 / 365 = 875.81, refund 2251.21 - 875.81 - 25.00 = **1350.40**.

**RF-04 Cancellation fee.** Cancelling after the free-look period costs **$25.00**, taken from the refund. The fee is
not refundable in any circumstances, including when the member later disputes the cancellation.

**RF-05 Fees are never refunded.** Installment fees (BR-06) and late fees (BR-11) are charges for a service already
given: they are not part of the premium paid and are never refunded, in the free-look period or after it.
