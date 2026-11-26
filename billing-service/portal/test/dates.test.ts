import assert from "node:assert/strict";
import { test } from "node:test";

import { daysBetween, dueLabel, formatDate, parseIsoDate } from "../src/dates.ts";

test("formatDate shows the short month name, day and year", () => {
  assert.equal(formatDate("2027-03-01"), "Mar 1, 2027");
  assert.equal(formatDate("2026-11-30"), "Nov 30, 2026");
});

test("parseIsoDate rejects malformed and impossible dates", () => {
  assert.deepEqual(parseIsoDate("2028-02-29"), { year: 2028, month: 2, day: 29 });
  assert.throws(() => parseIsoDate("2027-02-29"), /no such date/);
  assert.throws(() => parseIsoDate("11/03/2026"), /not an ISO date/);
});

test("daysBetween counts calendar days across months and years", () => {
  assert.equal(daysBetween("2026-11-03", "2026-11-30"), 27);
  assert.equal(daysBetween("2026-12-31", "2027-01-01"), 1);
  assert.equal(daysBetween("2026-11-03", "2026-10-20"), -14);
});

test("dueLabel wording", () => {
  const today = "2026-11-03";
  assert.equal(dueLabel("2026-11-03", today), "Due today");
  assert.equal(dueLabel("2026-11-04", today), "Due tomorrow");
  assert.equal(dueLabel("2026-11-08", today), "Due in 5 days");
  assert.equal(dueLabel("2026-11-02", today), "Overdue by 1 day");
  assert.equal(dueLabel("2026-11-01", today), "Overdue by 2 days");
});
