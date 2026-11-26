// Money display (PD-01 in docs/specs/portal-display.md). The API sends amounts as strings with two decimals.

const SYMBOLS: Record<string, string> = { USD: "$" };

/** "1234.5" or 1234.5 in USD -> "$1,234.50"; negatives -> "-$12.00"; other currencies -> "1,234.50 EUR". */
export function formatMoney(amount: string | number, currency = "USD"): string {
  const cents = toCents(amount);
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const dollars = Math.floor(abs / 100).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fraction = (abs % 100).toString().padStart(2, "0");
  const symbol = SYMBOLS[currency];
  return symbol ? `${sign}${symbol}${dollars}.${fraction}` : `${sign}${dollars}.${fraction} ${currency}`;
}

/** Whole cents, without going through binary floating point for strings. */
export function toCents(amount: string | number): number {
  const text = typeof amount === "number" ? amount.toFixed(2) : amount.trim();
  const match = /^(-)?(\d+)(?:\.(\d{1,2}))?$/.exec(text);
  if (!match) throw new Error(`not an amount: ${JSON.stringify(amount)}`);
  const [, minus, whole, frac = ""] = match;
  const cents = Number(whole) * 100 + Number(frac.padEnd(2, "0"));
  return minus ? -cents : cents;
}
