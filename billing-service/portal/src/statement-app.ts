// The member statement page (/portal/statement.html?customer=C-1001&month=2026-11). Without a month it shows the
// month of the service's "today".

import { formatMoney } from "./money.ts";
import { monthHeading, statementRows } from "./statement.ts";

const $ = (id: string): HTMLElement => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el;
};

async function getJson(url: string): Promise<any> {
  const response = await fetch(url);
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail ?? body.error ?? response.statusText);
  return body;
}

async function load(customerId: string, month: string | null): Promise<void> {
  const chosen = month ?? (await getJson("/health")).today.slice(0, 7);
  const statement = await getJson(
    `/customers/${encodeURIComponent(customerId)}/statement?month=${encodeURIComponent(chosen)}`);
  $("customer-name").textContent = statement.name;
  $("statement-month").textContent = monthHeading(statement.month);
  const rows = $("statement-rows");
  rows.replaceChildren();
  for (const row of statementRows(statement.lines)) {
    const tr = document.createElement("tr");
    for (const text of [row.policy, row.installment, row.due, row.amount, row.status]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    rows.append(tr);
  }
  $("statement-empty").hidden = statement.lines.length > 0;
  $("amount-due").textContent = formatMoney(statement.amount_due);
}

const params = new URLSearchParams(location.search);
const customerId = params.get("customer") ?? "C-1001";
load(customerId, params.get("month")).catch((error: Error) => {
  $("load-error").textContent = `Couldn't load the statement for ${customerId}: ${error.message}`;
});
