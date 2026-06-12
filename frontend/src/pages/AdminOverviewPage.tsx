import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  adminApi,
  getAdminToken,
  setAdminToken,
  type SignupRequestAdminView,
  type TenantAdminView,
  type TenantDetailAdminView,
} from "../api/admin";
import { ApiError } from "../api/client";
import type { DailyCostResponse } from "../api/types";
import { AdminTabs } from "../components/AdminTabs";
import { Button } from "../components/Button";
import { t } from "../i18n";

// Same semantic chart tokens as the owner dashboard (index.css) so the founder
// surface looks like part of the same product.
const C = {
  primary: "var(--color-primary)",
  accent: "var(--color-accent)",
  amber: "var(--color-amber)",
  destructive: "var(--color-destructive)",
  muted: "var(--color-muted-foreground)",
  grid: "var(--color-border)",
};
const DONUT_COLORS = [C.primary, C.accent, C.amber, C.muted, C.destructive];

/** "2026-06-11" → "11/6" for compact chart axes. */
function shortDay(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(d)}/${Number(m)}`;
}

/** "2026-06-11T..." → "6/26" month bucket for the growth chart. */
function monthBucket(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${String(d.getFullYear()).slice(2)}`;
}

/** Small-dollar amounts: LLM costs live in fractions of a cent. */
function fmtUsd(v: number): string {
  if (v >= 100) return `$${v.toFixed(0)}`;
  if (v >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toFixed(4)}`;
}

interface PerTenant {
  detail: TenantDetailAdminView | null;
  costs: DailyCostResponse | null;
}

// Founder platform overview: every number is an aggregation of per-tenant
// admin endpoints (the backend deliberately has no cross-tenant read — the
// Wall), so this page fans out one detail + one costs call per tenant and
// aggregates client-side. Fine at founder scale (tens of tenants).
export default function AdminOverviewPage() {
  const navigate = useNavigate();
  const [tenants, setTenants] = useState<TenantAdminView[] | null>(null);
  const [requests, setRequests] = useState<SignupRequestAdminView[] | null>(null);
  const [perTenant, setPerTenant] = useState<Map<string, PerTenant> | null>(null);
  const [partial, setPartial] = useState(false);
  const [error, setError] = useState(false);
  const [tick, setTick] = useState(0);

  // No admin token → bounce to the admin login (same rule as the other tabs).
  useEffect(() => {
    if (!getAdminToken()) navigate("/admin/login", { replace: true });
  }, [navigate]);

  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    void (async () => {
      let tenantRows: TenantAdminView[];
      try {
        const [tRows, rRows] = await Promise.all([
          adminApi.listTenants(),
          adminApi.listRequests(),
        ]);
        if (cancelled) return;
        tenantRows = tRows;
        setTenants(tRows);
        setRequests(rRows);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          setAdminToken(null);
          navigate("/admin/login", { replace: true });
        } else if (!cancelled) setError(true);
        return;
      }

      // Per-tenant fan-out — best-effort: a failing tenant degrades the
      // aggregate (flagged via the "partial" note) instead of blanking the page.
      const settled = await Promise.allSettled(
        tenantRows.map(async (row) => {
          const [detail, costs] = await Promise.allSettled([
            adminApi.tenantDetail(row.id),
            adminApi.tenantCosts(row.id),
          ]);
          return {
            id: row.id,
            detail: detail.status === "fulfilled" ? detail.value : null,
            costs: costs.status === "fulfilled" ? costs.value : null,
          };
        }),
      );
      if (cancelled) return;
      const map = new Map<string, PerTenant>();
      let missing = false;
      for (const s of settled) {
        if (s.status === "fulfilled") {
          map.set(s.value.id, { detail: s.value.detail, costs: s.value.costs });
          if (!s.value.detail || !s.value.costs) missing = true;
        } else missing = true;
      }
      setPerTenant(map);
      setPartial(missing);
    })();
    return () => {
      cancelled = true;
    };
  }, [tick, navigate]);

  function logout() {
    setAdminToken(null);
    navigate("/admin/login", { replace: true });
  }

  // ---- aggregations ----
  const activeCount = (tenants ?? []).filter((r) => r.is_active).length;
  const pendingCount = (requests ?? []).filter((r) => r.status === "pending").length;
  // Paid period lapsed but not suspended — the founder's collections list.
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  const pastDueCount = (tenants ?? []).filter(
    (r) => r.is_active && r.current_period_end && new Date(r.current_period_end) < now,
  ).length;

  const details = [...(perTenant?.values() ?? [])]
    .map((p) => p.detail)
    .filter((d): d is TenantDetailAdminView => d !== null);
  const ordersToday = details.reduce((sum, d) => sum + d.orders_today, 0);
  const totalCustomers = details.reduce((sum, d) => sum + d.customers_count, 0);

  const todayIso = new Date().toISOString().slice(0, 10);
  let cost30d = 0;
  let costToday = 0;
  const costByDay = new Map<string, number>();
  const costByTenant = new Map<string, number>();
  for (const [id, p] of perTenant ?? []) {
    for (const day of p.costs?.days ?? []) {
      cost30d += day.total_usd;
      if (day.date === todayIso) costToday += day.total_usd;
      costByDay.set(day.date, (costByDay.get(day.date) ?? 0) + day.total_usd);
      costByTenant.set(id, (costByTenant.get(id) ?? 0) + day.total_usd);
    }
  }

  const tenantName = new Map((tenants ?? []).map((r) => [r.id, r.name]));

  const costTrend = [...costByDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, usd]) => ({ day: shortDay(date), usd }));

  const topCostTenants = [...costByTenant.entries()]
    .sort(([, a], [, b]) => b - a)
    .slice(0, 7)
    .map(([id, usd]) => ({ name: tenantName.get(id) ?? "…", usd }));

  const ordersByTenant = details
    .map((d) => ({ name: d.name, orders: d.orders_today }))
    .sort((a, b) => b.orders - a.orders)
    .slice(0, 8);

  const customersByTenant = details
    .map((d) => ({ name: d.name, customers: d.customers_count }))
    .sort((a, b) => b.customers - a.customers)
    .slice(0, 8);

  // Cumulative shop count per signup month.
  const byMonth = new Map<string, number>();
  for (const row of [...(tenants ?? [])].sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  )) {
    const key = monthBucket(row.created_at);
    byMonth.set(key, (byMonth.get(key) ?? 0) + 1);
  }
  let running = 0;
  const growthData = [...byMonth.entries()].map(([month, n]) => {
    running += n;
    return { month, total: running };
  });

  const planData = [...(tenants ?? [])
    .reduce((acc, r) => acc.set(r.plan_tier, (acc.get(r.plan_tier) ?? 0) + 1), new Map<string, number>())
    .entries()].map(([name, value]) => ({ name, value }));

  const funnelData = [
    { name: t.statusPending, value: pendingCount, fill: C.amber },
    {
      name: t.statusApproved,
      value: (requests ?? []).filter((r) => r.status === "approved").length,
      fill: C.accent,
    },
    {
      name: t.statusRejected,
      value: (requests ?? []).filter((r) => r.status === "rejected").length,
      fill: C.destructive,
    },
  ];

  const loadingBase = tenants === null || requests === null;
  const loadingDetail = perTenant === null;

  return (
    <main className="min-h-dvh bg-background px-4 py-6">
      <div className="mx-auto flex max-w-5xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <h1 className="text-xl font-bold text-primary">{t.overviewTitle}</h1>
          <button
            onClick={logout}
            className="min-h-[44px] rounded-lg px-3 text-sm font-medium text-destructive hover:bg-destructive/10"
          >
            {t.logout}
          </button>
        </header>

        <AdminTabs active="overview" />

        {error ? (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
            <p className="text-destructive">{t.ovError}</p>
            <Button onClick={reload} className="bg-card !text-foreground ring-1 ring-border">
              {t.retry}
            </Button>
          </div>
        ) : loadingBase ? (
          <OverviewSkeleton />
        ) : (
          <>
            {partial && (
              <p className="rounded-xl border border-status-pending/40 bg-status-pending/10 px-4 py-3 text-sm text-status-pending">
                {t.ovPartialNote}
              </p>
            )}

            {/* KPI row */}
            <section className="grid grid-cols-2 gap-3 lg:grid-cols-3">
              <KpiCard
                label={t.ovTotalTenants}
                value={String((tenants ?? []).length)}
                hint={`${t.ovActiveTenants}: ${activeCount}`}
              />
              <KpiCard
                label={t.ovPendingRequests}
                value={String(pendingCount)}
                alert={pendingCount > 0}
              />
              <KpiCard
                label={t.ovPastDueTenants}
                value={String(pastDueCount)}
                alert={pastDueCount > 0}
              />
              <KpiCard
                label={t.ovOrdersToday}
                value={loadingDetail ? "…" : String(ordersToday)}
              />
              <KpiCard
                label={t.ovTotalCustomers}
                value={loadingDetail ? "…" : String(totalCustomers)}
              />
              <KpiCard
                label={t.ovAiCostToday}
                value={loadingDetail ? "…" : fmtUsd(costToday)}
              />
              <KpiCard
                label={t.ovAiCost30d}
                value={loadingDetail ? "…" : fmtUsd(cost30d)}
              />
            </section>

            {/* Platform charts (tenants + signup requests — already loaded) */}
            <section className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <ChartCard title={t.ovTenantGrowth} empty={growthData.length === 0}>
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={growthData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                    <defs>
                      <linearGradient id="growth" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={C.primary} stopOpacity={0.25} />
                        <stop offset="100%" stopColor={C.primary} stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke={C.grid} strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="month" tick={{ fontSize: 11 }} tickLine={false} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} width={28} />
                    <Tooltip formatter={(v) => [Number(v), t.ovTotalTenants]} />
                    <Area
                      type="monotone"
                      dataKey="total"
                      stroke={C.primary}
                      strokeWidth={2}
                      fill="url(#growth)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard title={t.ovSignupFunnel} empty={(requests ?? []).length === 0}>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={funnelData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                    <CartesianGrid stroke={C.grid} strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 12 }} tickLine={false} />
                    <YAxis allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} width={28} />
                    <Tooltip formatter={(v) => [Number(v), t.ovSignupFunnel]} />
                    <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                      {funnelData.map((d, i) => (
                        <Cell key={i} fill={d.fill} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard title={t.ovPlanMix} empty={planData.length === 0}>
                <ResponsiveContainer width="100%" height={220}>
                  <PieChart>
                    <Pie
                      data={planData}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={55}
                      outerRadius={80}
                      paddingAngle={2}
                    >
                      {planData.map((_, i) => (
                        <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v) => [Number(v), t.ovTotalTenants]} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </ChartCard>

              {/* Aggregated charts (per-tenant fan-out — may arrive a moment later) */}
              {loadingDetail ? (
                <div className="h-64 animate-pulse rounded-2xl border border-border bg-muted/50" aria-hidden />
              ) : (
                <ChartCard title={t.ovCostTrend} empty={costTrend.length === 0}>
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={costTrend} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                      <CartesianGrid stroke={C.grid} strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="day" tick={{ fontSize: 11 }} tickLine={false} />
                      <YAxis
                        tickFormatter={(v: number) => fmtUsd(v)}
                        tick={{ fontSize: 10 }}
                        tickLine={false}
                        width={56}
                      />
                      <Tooltip formatter={(v) => [fmtUsd(Number(v)), t.ovAiCostToday]} />
                      <Bar dataKey="usd" fill={C.primary} radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </ChartCard>
              )}

              {!loadingDetail && (
                <>
                  <ChartCard title={t.ovTopCostTenants} empty={topCostTenants.length === 0}>
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart
                        layout="vertical"
                        data={topCostTenants}
                        margin={{ top: 8, right: 8, left: 8, bottom: 0 }}
                      >
                        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" horizontal={false} />
                        <XAxis
                          type="number"
                          tickFormatter={(v: number) => fmtUsd(v)}
                          tick={{ fontSize: 10 }}
                          tickLine={false}
                        />
                        <YAxis type="category" dataKey="name" width={90} tick={{ fontSize: 12 }} tickLine={false} />
                        <Tooltip formatter={(v) => [fmtUsd(Number(v)), t.ovAiCost30d]} />
                        <Bar dataKey="usd" fill={C.amber} radius={[0, 4, 4, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </ChartCard>

                  <ChartCard title={t.ovOrdersByTenant} empty={ordersByTenant.length === 0}>
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart
                        layout="vertical"
                        data={ordersByTenant}
                        margin={{ top: 8, right: 8, left: 8, bottom: 0 }}
                      >
                        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" horizontal={false} />
                        <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} />
                        <YAxis type="category" dataKey="name" width={90} tick={{ fontSize: 12 }} tickLine={false} />
                        <Tooltip formatter={(v) => [Number(v), t.ovOrdersToday]} />
                        <Bar dataKey="orders" fill={C.accent} radius={[0, 4, 4, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </ChartCard>

                  <ChartCard title={t.ovCustomersByTenant} empty={customersByTenant.length === 0}>
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart
                        layout="vertical"
                        data={customersByTenant}
                        margin={{ top: 8, right: 8, left: 8, bottom: 0 }}
                      >
                        <CartesianGrid stroke={C.grid} strokeDasharray="3 3" horizontal={false} />
                        <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} />
                        <YAxis type="category" dataKey="name" width={90} tick={{ fontSize: 12 }} tickLine={false} />
                        <Tooltip formatter={(v) => [Number(v), t.ovTotalCustomers]} />
                        <Bar dataKey="customers" fill={C.primary} radius={[0, 4, 4, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </ChartCard>
                </>
              )}
            </section>
          </>
        )}
      </div>
    </main>
  );
}

function KpiCard({
  label,
  value,
  hint,
  alert,
}: {
  label: string;
  value: string;
  hint?: string;
  alert?: boolean;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={`tabular mt-1 truncate text-lg font-semibold ${
          alert ? "text-status-pending" : "text-foreground"
        }`}
      >
        {value}
      </p>
      {hint && <p className="mt-0.5 truncate text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

// Charts read left-to-right (dates, numbers), so the inner container is LTR
// while the title stays in the page's RTL flow (same as the owner dashboard).
function ChartCard({
  title,
  empty,
  children,
}: {
  title: string;
  empty?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <h3 className="mb-2 text-sm font-semibold text-foreground">{title}</h3>
      {empty ? (
        <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
          {t.dashNoData}
        </div>
      ) : (
        <div dir="ltr">{children}</div>
      )}
    </div>
  );
}

function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-hidden>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded-2xl border border-border bg-muted/50" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-64 animate-pulse rounded-2xl border border-border bg-muted/50" />
        ))}
      </div>
    </div>
  );
}
