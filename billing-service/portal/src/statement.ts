// The monthly statement (PD-05 in docs/specs/portal-display.md), from GET /customers/{id}/statement?month=.

import { formatDate } from "./dates.ts";
import { formatMoney } from "./money.ts";

export interface StatementLine {
  policy_id: string;
  installment: number;
  label: string;
  due_date: string;
  total: string;
  paid: boolean;
}

export interface StatementRow {
  policy: string;
  installment: string;
  due: string;
  amount: string;
  status: string;
}

const MONTH = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

/** "2026-11" -> "November 2026". */
export function monthHeading(month: string): string {
  const match = /^(\d{4})-(0[1-9]|1[0-2])$/.exec(month);
  if (!match) throw new Error(`not a month: ${JSON.stringify(month)}`);
  const [year, monthNumber] = match.slice(1).map(Number);
  return MONTH.format(new Date(Date.UTC(year, monthNumber, 1)));
}

/** One display row per statement line: amounts and dates as members read them. */
export function statementRows(lines: StatementLine[]): StatementRow[] {
  return lines.map((line) => ({
    policy: line.policy_id,
    installment: line.label,
    due: formatDate(line.due_date),
    amount: formatMoney(line.total),
    status: line.paid ? "Paid" : "Unpaid",
  }));
}
