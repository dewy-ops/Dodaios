function dayStart(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function parseDueDate(dueAt: string): Date {
  // ECMAScript interprets a bare YYYY-MM-DD as UTC. A reminder date without a
  // time is a local calendar date, otherwise users west of UTC see it on the
  // previous day.
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dueAt);
  if (!dateOnly) return new Date(dueAt);
  const [, year, month, day] = dateOnly;
  const due = new Date(Number(year), Number(month) - 1, Number(day));
  if (
    due.getFullYear() !== Number(year) ||
    due.getMonth() !== Number(month) - 1 ||
    due.getDate() !== Number(day)
  ) {
    return new Date(Number.NaN);
  }
  return due;
}

/** "Overdue", "Today", "Tomorrow" or a short weekday and date. */
export function dueLabel(dueAt: string, now: Date, t: (key: string) => string, locale: string): string {
  const due = parseDueDate(dueAt);
  if (Number.isNaN(due.getTime())) return "";
  const days = Math.round((dayStart(due) - dayStart(now)) / 86_400_000);
  const hasTime = dueAt.length > 10;
  const time = hasTime ? " " + due.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" }) : "";
  if (days < 0 || (days === 0 && hasTime && due.getTime() < now.getTime())) return t("sidebar.due.overdue");
  if (days === 0) return t("sidebar.due.today") + time;
  if (days === 1) return t("sidebar.due.tomorrow") + time;
  return due.toLocaleDateString(locale, { weekday: "short", day: "numeric", month: "short" });
}
