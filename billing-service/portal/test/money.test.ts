import assert from "node:assert/strict";
import { test } from "node:test";

import { formatMoney, splitCents, toCents } from "../src/money.ts";

test("USD amounts get a symbol, thousands separators and two decimals", () => {
  assert.equal(formatMoney("1411.60"), "$1,411.60");
  assert.equal(formatMoney("120.67"), "$120.67");
  assert.equal(formatMoney("1234567.89"), "$1,234,567.89");
  assert.equal(formatMoney("0.00"), "$0.00");
});

test("numbers and one-decimal strings are accepted", () => {
  assert.equal(formatMoney(1234.5), "$1,234.50");
  assert.equal(formatMoney("12.5"), "$12.50");
  assert.equal(formatMoney("7"), "$7.00");
});

test("negative amounts put the sign before the symbol", () => {
  assert.equal(formatMoney("-12.00"), "-$12.00");
});

test("other currencies show the code after the amount", () => {
  assert.equal(formatMoney("1000.10", "EUR"), "1,000.10 EUR");
});

test("toCents parses without floating point drift", () => {
  assert.equal(toCents("1.10"), 110);
  assert.equal(toCents(" 19.99 "), 1999);
  assert.equal(toCents("-0.5"), -50);
  assert.throws(() => toCents("12.345"), /not an amount/);
  assert.throws(() => toCents("abc"), /not an amount/);
});

test("splitCents groups the dollars and keeps two digits of cents", () => {
  assert.deepEqual(splitCents(123456), { dollars: "1,234", fraction: "56" });
  assert.deepEqual(splitCents(100000000), { dollars: "1,000,000", fraction: "00" });
  assert.deepEqual(splitCents(50), { dollars: "0", fraction: "50" });
});
