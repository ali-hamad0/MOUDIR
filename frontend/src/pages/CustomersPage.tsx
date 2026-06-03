import { useEffect, useMemo, useState } from "react";

import { customersApi } from "../api/customers";
import { ApiError } from "../api/client";
import type { CustomerRead, CustomersPage as Page } from "../api/types";
import { Button } from "../components/Button";
import { formatDate, formatLbp, formatUsd } from "../format";
import { t } from "../i18n";

const PAGE_SIZE = 20;

type SortKey = "total_spent_lbp" | "last_order_at";
type SortDir = "asc" | "desc";

export default function CustomersPage() {
  const [page, setPage] = useState<Page | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("last_order_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    customersApi
      .list(PAGE_SIZE, offset)
      .then((data) => !cancelled && setPage(data))
      .catch((err) => {
        if (!cancelled) setError(!(err instanceof ApiError && err.status === 401));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [offset, reloadTick]);

  // Sort the current page client-side (the page is small; the backend already
  // returns most-recently-active first as the default).
  const rows = useMemo(() => {
    if (!page) return [];
    const sorted = [...page.items].sort((a, b) => {
      const av = sortVal(a, sortKey);
      const bv = sortVal(b, sortKey);
      return sortDir === "asc" ? av - bv : bv - av;
    });
    return sorted;
  }, [page, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const total = page?.total ?? 0;
  const pageNum = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-xl font-bold text-foreground">{t.customersTitle}</h2>

      {loading ? (
        <Skeletons />
      ) : error && !page ? (
        <ErrorState onRetry={() => setReloadTick((n) => n + 1)} />
      ) : rows.length === 0 ? (
        <EmptyState />
      ) : (
        <>
          {/* Mobile: cards. Desktop (sm+): sortable table. */}
          <ul className="flex flex-col gap-3 sm:hidden">
            {rows.map((c) => (
              <CustomerCard key={c.id} c={c} />
            ))}
          </ul>
          <div className="hidden overflow-x-auto sm:block">
            <table className="w-full text-start text-sm">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="py-2 text-start font-medium">{t.colCustomer}</th>
                  <th className="py-2 text-start font-medium">{t.colOrders}</th>
                  <SortableTh
                    label={t.colTotalSpent}
                    active={sortKey === "total_spent_lbp"}
                    dir={sortDir}
                    onClick={() => toggleSort("total_spent_lbp")}
                  />
                  <SortableTh
                    label={t.colLastOrder}
                    active={sortKey === "last_order_at"}
                    dir={sortDir}
                    onClick={() => toggleSort("last_order_at")}
                  />
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id} className="border-b border-border last:border-0">
                    <td className="py-3">
                      <div className="font-medium text-foreground">
                        {c.display_name || t.unknownCustomer}
                      </div>
                      <div className="tabular text-xs text-muted-foreground" dir="ltr">
                        {c.phone_number}
                      </div>
                    </td>
                    <td className="tabular py-3">{c.order_count}</td>
                    <td className="py-3">
                      <Money lbp={c.total_spent_lbp} usd={c.total_spent_usd} />
                    </td>
                    <td className="tabular py-3">{formatDate(c.last_order_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Pager
            pageNum={pageNum}
            pageCount={pageCount}
            onPrev={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            onNext={() => setOffset((o) => o + PAGE_SIZE)}
          />
        </>
      )}
    </div>
  );
}

function sortVal(c: CustomerRead, key: SortKey): number {
  if (key === "total_spent_lbp") return c.total_spent_lbp;
  return c.last_order_at ? new Date(c.last_order_at).getTime() : 0;
}

function Money({ lbp, usd }: { lbp: number; usd: string | null }) {
  const usdStr = formatUsd(usd);
  return (
    <span className="flex flex-col">
      <span className="tabular font-medium text-foreground">{formatLbp(lbp)}</span>
      {usdStr && <span className="tabular text-xs text-muted-foreground">{usdStr}</span>}
    </span>
  );
}

function CustomerCard({ c }: { c: CustomerRead }) {
  return (
    <li className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold text-foreground">
            {c.display_name || t.unknownCustomer}
          </p>
          <p className="tabular text-xs text-muted-foreground" dir="ltr">
            {c.phone_number}
          </p>
        </div>
        <Money lbp={c.total_spent_lbp} usd={c.total_spent_usd} />
      </div>
      <div className="mt-2 flex justify-between border-t border-border pt-2 text-xs text-muted-foreground">
        <span>
          {t.colOrders}: <span className="tabular">{c.order_count}</span>
        </span>
        <span>
          {t.colLastOrder}: <span className="tabular">{formatDate(c.last_order_at)}</span>
        </span>
      </div>
    </li>
  );
}

function SortableTh({
  label,
  active,
  dir,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: SortDir;
  onClick: () => void;
}) {
  return (
    <th
      className="py-2 text-start font-medium"
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={onClick}
        className="inline-flex items-center gap-1 hover:text-foreground"
      >
        {label}
        <span aria-hidden className="text-xs">
          {active ? (dir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );
}

function Pager({
  pageNum,
  pageCount,
  onPrev,
  onNext,
}: {
  pageNum: number;
  pageCount: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (pageCount <= 1) return null;
  return (
    <div className="flex items-center justify-between gap-3">
      <button
        type="button"
        onClick={onPrev}
        disabled={pageNum <= 1}
        className="min-h-[44px] rounded-lg px-4 text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-40"
      >
        {t.prevPage}
      </button>
      <span className="tabular text-sm text-muted-foreground">
        {t.pageOf.replace("{n}", String(pageNum)).replace("{total}", String(pageCount))}
      </span>
      <button
        type="button"
        onClick={onNext}
        disabled={pageNum >= pageCount}
        className="min-h-[44px] rounded-lg px-4 text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-40"
      >
        {t.nextPage}
      </button>
    </div>
  );
}

function Skeletons() {
  return (
    <ul className="flex flex-col gap-3" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <li key={i} className="h-16 animate-pulse rounded-2xl border border-border bg-muted/50" />
      ))}
    </ul>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center">
      <p className="font-medium text-foreground">{t.noCustomersYet}</p>
      <p className="mt-1 text-sm text-muted-foreground">{t.noCustomersHint}</p>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
      <p className="text-destructive">{t.customersError}</p>
      <Button onClick={onRetry} className="bg-card !text-foreground ring-1 ring-border">
        {t.retry}
      </Button>
    </div>
  );
}
