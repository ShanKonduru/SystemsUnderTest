import assert from "node:assert/strict";
import { test } from "node:test";

import { monthHeading, statementRows } from "../src/statement.ts";

test("statement rows", () => {
  const rows = statementRows([
    { policy_id: "P-5002", installment: 3, label: "3rd installment", due_date: "2026-10-01", total: "321.16", paid: true },
    { policy_id: "P-5001", installment: 10, label: "10th installment", due_date: "2026-10-31", total: "1120.63",
      paid: false },
  ]);
  assert.deepEqual(rows, [
    { policy: "P-5002", installment: "3rd installment", due: "Oct 1, 2026", amount: "$321.16", status: "Paid" },
    { policy: "P-5001", installment: "10th installment", due: "Oct 31, 2026", amount: "$1,120.63", status: "Unpaid" },
  ]);
  assert.deepEqual(statementRows([]), []);
});

test("a month must look like 2026-11", () => {
  for (const bad of ["2026-13", "2026-1", "Nov 2026"]) assert.throws(() => monthHeading(bad), /not a month/);
});
