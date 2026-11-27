// Payment form validation (PD-04 in docs/specs/portal-display.md). Returns every problem, not just the first.

import { toCents } from "./money.ts";

export interface PaymentForm {
  amount: string;
  amountDue: string;
  cardNumber: string;
  cvv: string;
  expMonth: number;
  expYear: number;
  zip: string;
}

export function validatePayment(form: PaymentForm, todayIso: string): string[] {
  const errors: string[] = [];
  let cents: number | null = null;
  try {
    cents = toCents(form.amount);
  } catch {
    errors.push("Enter the amount as dollars and cents, like 120.50.");
  }
  if (cents !== null && cents !== toCents(form.amountDue)) {
    errors.push(`The amount must be exactly the amount due, ${form.amountDue}.`);
  }
  const digits = form.cardNumber.replace(/[\s-]/g, "");
  if (!/^\d{13,19}$/.test(digits) || !luhnValid(digits)) errors.push("Enter a valid card number.");
  const codeLength = securityCodeLength(digits);
  const code = form.cvv.trim();
  if (!/^\d+$/.test(code) || code.length !== codeLength) {
    errors.push(`Enter the ${codeLength}-digit security code.`);
  }
  if (!Number.isInteger(form.expMonth) || form.expMonth < 1 || form.expMonth > 12) {
    errors.push("Enter the expiry month as 1 to 12.");
  } else if (isExpired(form.expMonth, form.expYear, todayIso)) {
    errors.push("This card has expired.");
  }
  if (!/^\d{5}$/.test(form.zip)) errors.push("Enter a 5-digit ZIP code.");
  return errors;
}

/** American Express cards have a 4-digit security code on the front; other cards 3 digits on the back. */
export function securityCodeLength(cardDigits: string): number {
  return cardDigits.startsWith("34" || "37") ? 4 : 3;
}

/** A card is valid through the last day of its expiry month. */
export function isExpired(expMonth: number, expYear: number, todayIso: string): boolean {
  const [year, month] = todayIso.split("-").map(Number);
  return expYear < year || (expYear === year && expMonth < month);
}

export function luhnValid(digits: string): boolean {
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
  }
  return sum % 10 === 0;
}
