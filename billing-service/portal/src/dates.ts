// Date display (PD-02, PD-03 in docs/specs/portal-display.md). Dates are ISO calendar dates ("2026-11-03") with no
// time or zone, so all arithmetic is done in UTC to keep the member's own timezone out of it.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export interface CalendarDate {
  year: number;
  month: number; // 1-12
  day: number;
}

export function parseIsoDate(iso: string): CalendarDate {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) throw new Error(`not an ISO date: ${JSON.stringify(iso)}`);
  const [year, month, day] = match.slice(1).map(Number);
  const probe = new Date(Date.UTC(year, month - 1, day));
  if (probe.getUTCMonth() !== month - 1 || probe.getUTCDate() !== day) throw new Error(`no such date: ${iso}`);
  return { year, month, day };
}

/** "2027-03-01" -> "Mar 1, 2027". */
export function formatDate(iso: string): string {
  const { year, month, day } = parseIsoDate(iso);
  return `${MONTHS[month - 1]} ${day}, ${year}`;
}

/** Whole days from `fromIso` to `toIso` (negative when `toIso` is earlier). */
export function daysBetween(fromIso: string, toIso: string): number {
  const utc = ({ year, month, day }: CalendarDate) => Date.UTC(year, month - 1, day);
  return Math.round((utc(parseIsoDate(toIso)) - utc(parseIsoDate(fromIso))) / 86_400_000);
}

/** "Due today", "Due tomorrow", "Due in 5 days", "Overdue by 1 day", "Overdue by 3 days". */
export function dueLabel(dueIso: string, todayIso: string): string {
  const days = daysBetween(todayIso, dueIso);
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  if (days > 1) return `Due in ${days} days`;
  return `Overdue by ${-days} ${days === -1 ? "day" : "days"}`;
}
