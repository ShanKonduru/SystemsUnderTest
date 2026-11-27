---
order: 21
title: "Portal won't take the 4-digit security code on some American Express cards"
labels: [rca-lab, bug]
---
**Reported by:** Sofia Alvarez, Member Portal Support

**Version:** billing service 1.4.0

### What happened

Several members paying with American Express can't get past the payment form on the billing page
(`/portal?policy=P-5001`). They enter the 4-digit code printed on the front of the card and the form says:

> Enter the 3-digit security code.

If they enter only 3 digits, the form accepts it, but that isn't their code. It doesn't happen to every AmEx
card: a member whose card number starts with 34 paid fine with her 4-digit code. The ones that fail start with 37.

To reproduce with test card numbers (expiry 12/2027, ZIP 07030, amount 120.63):

| Card number | Security code | Form says |
|---|---|---|
| 3400 000000 00009 | 1234 | (accepted) |
| 3782 822463 10005 | 1234 | Enter the 3-digit security code. |
| 3714 496353 98431 | 1234 | Enter the 3-digit security code. |
| 4111 1111 1111 1111 | 123 | (accepted) |

### Expected

Every American Express card takes its 4-digit security code.
