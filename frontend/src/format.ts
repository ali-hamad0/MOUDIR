// Money + time formatting. Both currencies come straight from the order's
// stored snapshots (Phase 2) — the dashboard does NO live FX conversion. LBP is
// the primary figure; USD is shown muted/secondary. Western digits + grouping,
// applied consistently so tabular columns line up (ui-ux-pro-max number-tabular).

const lbpFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const usdFmt = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** "75,000 ل.ل." — primary money figure. Null → em dash. */
export function formatLbp(value: number | null | undefined): string {
  if (value == null) return "—";
  return `${lbpFmt.format(value)} ل.ل.`;
}

/** "$8.40" — secondary money figure. Null → empty (caller hides it). */
export function formatUsd(value: string | number | null | undefined): string {
  if (value == null || value === "") return "";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return "";
  return `$${usdFmt.format(n)}`;
}

/** Short Beirut-time label for an order timestamp, e.g. "14:05". */
export function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Beirut",
  });
}

/** Short Beirut date, e.g. "03/06/2026". Null → em dash. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "Asia/Beirut",
  });
}
