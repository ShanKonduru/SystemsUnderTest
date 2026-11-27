// The member billing page (/portal?policy=P-5001): policy summary, installment schedule and a payment form.

import { dueLabel, formatDate } from "./dates.ts";
import { formatMoney } from "./money.ts";
import { validatePayment } from "./validate.ts";

interface Installment {
  number: number;
  due_date: string;
  total: string;
  paid_on: string | null;
}

interface NextPayment {
  installment: number;
  due_date: string;
  late_fee: string;
  amount_due: string;
}

const $ = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing #${id}`);
  return el as T;
};

async function getJson(url: string): Promise<any> {
  const response = await fetch(url);
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail ?? body.error ?? response.statusText);
  return body;
}

function cell(text: string): HTMLTableCellElement {
  const td = document.createElement("td");
  td.textContent = text;
  return td;
}

async function load(policyId: string): Promise<void> {
  const [health, policy, schedule] = await Promise.all([
    getJson("/health"),
    getJson(`/policies/${encodeURIComponent(policyId)}`),
    getJson(`/policies/${encodeURIComponent(policyId)}/schedule`),
  ]);
  const today: string = health.today;
  $("policy-id").textContent = policy.id;
  $("product").textContent = `${policy.product} (${policy.coverage})`;
  $("premium").textContent = formatMoney(policy.annual_premium);
  $("status").textContent = policy.status.replace("_", " ");
  $("renewal").textContent = formatDate(policy.renewal_date);

  const rows = $("schedule-rows");
  rows.replaceChildren();
  const installments: Installment[] = schedule.installments;
  for (const inst of installments) {
    const tr = document.createElement("tr");
    tr.append(cell(String(inst.number)), cell(formatDate(inst.due_date)), cell(formatMoney(inst.total)),
      cell(inst.paid_on ? `Paid ${formatDate(inst.paid_on)}` : dueLabel(inst.due_date, today)));
    rows.append(tr);
  }

  const next: NextPayment | null = policy.next_payment;
  const form = $<HTMLFormElement>("pay-form");
  if (!next) {
    $("next-due").textContent = policy.status === "lapsed" ? "Policy lapsed: call us to reinstate it" : "Nothing due";
    $("next-due-label").textContent = "";
    form.hidden = true;
    return;
  }
  const lateNote = next.late_fee === "0.00" ? "" : ` (includes a ${formatMoney(next.late_fee)} late fee)`;
  $("next-due").textContent = `${formatMoney(next.amount_due)} on ${formatDate(next.due_date)}${lateNote}`;
  $("next-due-label").textContent = dueLabel(next.due_date, today);
  form.hidden = false;
  form.onsubmit = async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const errors = validatePayment({
      amount: String(data.get("amount") ?? ""),
      amountDue: next.amount_due,
      cardNumber: String(data.get("card") ?? ""),
      cvv: String(data.get("cvv") ?? ""),
      expMonth: Number(data.get("exp-month")),
      expYear: Number(data.get("exp-year")),
      zip: String(data.get("zip") ?? ""),
    }, today);
    const list = $("pay-errors");
    list.replaceChildren(...errors.map((text) => Object.assign(document.createElement("li"), { textContent: text })));
    $("pay-result").textContent = "";
    if (errors.length) return;
    // The page has no clock of its own: it pays at noon Eastern on the service's "today".
    const response = await fetch(`/policies/${encodeURIComponent(policyId)}/payments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: String(data.get("amount")), received_at: `${today}T17:00:00Z` }),
    });
    const body = await response.json();
    $("pay-result").textContent = response.ok
      ? `Paid ${formatMoney(body.amount)} for installment ${body.installment}.`
      : `Payment refused: ${body.detail ?? body.error}`;
    if (response.ok) await load(policyId);
  };
}

const policyId = new URLSearchParams(location.search).get("policy") ?? "P-5001";
load(policyId).catch((error: Error) => {
  $("load-error").textContent = `Couldn't load policy ${policyId}: ${error.message}`;
});
