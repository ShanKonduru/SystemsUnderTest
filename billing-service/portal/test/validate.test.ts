import assert from "node:assert/strict";
import { test } from "node:test";

import { isExpired, luhnValid, securityCodeLength, validatePayment, type PaymentForm } from "../src/validate.ts";

const TODAY = "2026-11-03";
const good: PaymentForm = {
  amount: "120.63", amountDue: "120.63", cardNumber: "4111 1111 1111 1111", cvv: "123", expMonth: 11, expYear: 2026,
  zip: "07030",
};

test("a correct form has no errors", () => {
  assert.deepEqual(validatePayment(good, TODAY), []);
});

test("the amount must parse and equal the amount due", () => {
  assert.deepEqual(validatePayment({ ...good, amount: "12O.63" }, TODAY),
    ["Enter the amount as dollars and cents, like 120.50."]);
  assert.deepEqual(validatePayment({ ...good, amount: "100.00" }, TODAY),
    ["The amount must be exactly the amount due, 120.63."]);
});

test("card number, expiry and ZIP are checked together", () => {
  const errors = validatePayment({ ...good, cardNumber: "4111 1111 1111 1112", expMonth: 13, zip: "7030" }, TODAY);
  assert.deepEqual(errors, ["Enter a valid card number.", "Enter the expiry month as 1 to 12.", "Enter a 5-digit ZIP code."]);
  assert.deepEqual(validatePayment({ ...good, expMonth: 10 }, TODAY), ["This card has expired."]);
});

test("the security code has 3 digits, 4 on American Express", () => {
  assert.deepEqual(validatePayment({ ...good, cvv: "12" }, TODAY), ["Enter the 3-digit security code."]);
  assert.deepEqual(validatePayment({ ...good, cvv: "1234" }, TODAY), ["Enter the 3-digit security code."]);
  const amex = { ...good, cardNumber: "3400 000000 00009" };
  assert.deepEqual(validatePayment({ ...amex, cvv: "1234" }, TODAY), []);
  assert.deepEqual(validatePayment({ ...amex, cvv: "123" }, TODAY), ["Enter the 4-digit security code."]);
  assert.equal(securityCodeLength("5555555555554444"), 3);
});

test("a card is valid through the end of its expiry month", () => {
  assert.equal(isExpired(11, 2026, TODAY), false);
  assert.equal(isExpired(10, 2026, TODAY), true);
  assert.equal(isExpired(1, 2027, TODAY), false);
  assert.equal(isExpired(12, 2025, TODAY), true);
});

test("Luhn check", () => {
  assert.equal(luhnValid("4111111111111111"), true);
  assert.equal(luhnValid("5555555555554444"), true);
  assert.equal(luhnValid("5555555555554445"), false);
});
