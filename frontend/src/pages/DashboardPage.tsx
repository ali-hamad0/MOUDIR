import { useEffect, useState } from "react";
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

import { dashboardApi, type DashboardSummary } from "../api/dashboard";
import { customersApi } from "../api/customers";
import { inventoryApi } from "../api/inventory";
import { predictionsApi } from "../api/predictions";
import type {
  AnomalyPredictions,
  ChurnPredictions,
  CustomersPage,
  DemandPredictions,
  InventoryPage,
} from "../api/types";
import { useAuth } from "../auth/context";
import { ProLockCard } from "../components/ProLock";
import { formatLbp } from "../format";
import { planLabel, statusMeta, t } from "../i18n";

// Chart palette — the SAME semantic tokens the rest of the UI uses (index.css),
// passed to recharts as CSS variables so light/dark stay consistent.
const C = {
  primary: "var(--color-primary)",
  accent: "var(--color-accent)",
  amber: "var(--color-amber)",
  destructive: "var(--color-destructive)",
  muted: "var(--color-muted-foreground)",
  grid: "var(--color-border)",
};

const DONUT_COLORS = [C.primary, C.accent, C.amber, C.muted, C.destructive];

// Risk at/above this counts a customer as "at risk" (mirrors the churn page).
const RISK_THRESHOLD = 0.5;

/** "2026-06-11" → "11/6" for compact chart axes. */
function shortDay(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(d)}/${Number(m)}`;
}

/** Compact LBP for axis ticks: 1500000 → "1.5M", 45000 → "45K". */
function compactLbp(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 1_000) return `${Math.round(v / 1_000)}K`;
  return String(v);
}

// Phone-width check for chart sizing: a 110px category axis is fine inside a
// half-width desktop card but eats a third of a 375px screen.
function useIsNarrow(): boolean {
  const [narrow, setNarrow] = useState(
    () => window.matchMedia("(max-width: 639px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 639px)");
    const onChange = (e: MediaQueryListEvent) => setNarrow(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return narrow;
}

// Home view inside the app shell: KPI cards + the chart grid. One summary call
// powers the trends; predictions/inventory/customers enrich the AI panels.
export default function DashboardPage() {
  const { me } = useAuth();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [demand, setDemand] = useState<DemandPredictions | null>(null);
  const [churn, setChurn] = useState<ChurnPredictions | null>(null);
  const [anomaly, setAnomaly] = useState<AnomalyPredictions | null>(null);
  const [inventory, setInventory] = useState<InventoryPage | null>(null);
  const [customers, setCustomers] = useState<CustomersPage | null>(null);
  const [error, setError] = useState(false);
  const isNarrow = useIsNarrow();
  // Width of the product/customer name axis on horizontal bar charts.
  const catAxisWidth = isNarrow ? 84 : 110;
  // ML insights are Pro (plan_gate enforces server-side with 402); for Free we
  // skip the calls entirely and show the lock cards instead.
  const isPro = me?.effective_plan === "pro";

  useEffect(() => {
    void (async () => {
      // The summary is the page's backbone — fail visibly if it fails. The
      // AI/enrichment calls are best-effort: a missing panel must not blank
      // the whole dashboard.
      try {
        setSummary(await dashboardApi.summary());
      } catch {
        setError(true);
      }
      const soft = await Promise.allSettled([
        isPro ? predictionsApi.demand() : Promise.reject(new Error("locked")),
        isPro ? predictionsApi.churn() : Promise.reject(new Error("locked")),
        isPro ? predictionsApi.anomaly(14) : Promise.reject(new Error("locked")),
        inventoryApi.list(100, 0),
        customersApi.list(100, 0),
      ]);
      if (soft[0].status === "fulfilled") setDemand(soft[0].value);
      if (soft[1].status === "fulfilled") setChurn(soft[1].value);
      if (soft[2].status === "fulfilled") setAnomaly(soft[2].value);
      if (soft[3].status === "fulfilled") setInventory(soft[3].value);
      if (soft[4].status === "fulfilled") setCustomers(soft[4].value);
    })();
  }, [isPro]);

  // ---- joins for the AI panels (id → display name) ----
  const productName = new Map(
    (inventory?.items ?? []).map((i) => [i.product_id, i.name_ar]),
  );
  const customerName = new Map(
    (customers?.items ?? []).map((c) => [c.id, c.display_name || c.phone_number]),
  );

  const demandData = (demand?.items ?? [])
    .filter((d) => d.predicted_units !== null)
    .map((d) => ({
      name: productName.get(d.product_id) ?? "…",
      units: d.predicted_units as number,
    }))
    .sort((a, b) => b.units - a.units)
    .slice(0, 8);

  const churnData = (churn?.items ?? [])
    .filter((c) => c.risk !== null)
    .sort((a, b) => (b.risk as number) - (a.risk as number))
    .slice(0, 5)
    .map((c) => ({
      name: customerName.get(c.customer_id) ?? "…",
      risk: Math.round((c.risk as number) * 100),
    }));

  const atRiskCount = (churn?.items ?? []).filter(
    (c) => (c.risk ?? 0) >= RISK_THRESHOLD,
  ).length;

  const inventoryData = (inventory?.items ?? [])
    .slice(0, 10)
    .map((i) => ({
      name: i.name_ar,
      quantity: i.quantity,
      is_low: i.is_low,
    }));

  const anomalyData = (anomaly?.items ?? []).map((d) => ({
    day: shortDay(d.day),
    revenue: d.revenue_lbp,
    anomalous: d.is_anomalous === true,
  }));

  const trendData = (summary?.daily ?? []).map((p) => ({
    day: shortDay(p.day),
    revenue: p.revenue_lbp,
    orders: p.orders,
  }));

  const statusData = (summary?.order_status ?? []).map((s) => ({
    name: statusMeta(s.status).label,
    value: s.count,
  }));

  if (error) {
    return <p className="text-destructive">{t.billsError}</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-xl font-bold text-foreground">{t.navHome}</h2>

      {/* KPI row */}
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label={t.dashTodayRevenue}
          value={summary ? formatLbp(summary.today_revenue_lbp) : "…"}
        />
        <KpiCard
          label={t.dashTodayOrders}
          value={summary ? String(summary.today_orders) : "…"}
        />
        <KpiCard
          label={t.dashLowStock}
          value={summary ? `${summary.low_stock_count} / ${summary.total_products}` : "…"}
          alert={(summary?.low_stock_count ?? 0) > 0}
        />
        <KpiCard label={t.dashAtRiskCustomers} value={String(atRiskCount)} alert={atRiskCount > 0} />
      </section>

      {/* Shop identity strip (kept from the original view) */}
      {me && (
        <section className="grid grid-cols-2 gap-3">
          <KpiCard label={t.shop} value={me.business_name ?? "—"} />
          <KpiCard label={t.plan} value={planLabel(me.plan_tier)} />
        </section>
      )}

      {/* Charts grid — pulse placeholders while the summary loads, so the page
          never shows bare empty axis frames (ux loading-chart). */}
      {summary === null ? (
        <section className="grid grid-cols-1 gap-3 lg:grid-cols-2" aria-hidden>
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-64 animate-pulse rounded-2xl border border-border bg-muted/50"
            />
          ))}
        </section>
      ) : (
      <section className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <ChartCard title={t.dashRevenueTrend}>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={trendData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
              <defs>
                <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={C.primary} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={C.primary} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={C.grid} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="day" tick={{ fontSize: 11 }} tickLine={false} />
              <YAxis tickFormatter={compactLbp} tick={{ fontSize: 11 }} tickLine={false} width={44} />
              <Tooltip formatter={(v) => [formatLbp(Number(v)), t.dashTodayRevenue]} />
              <Area
                type="monotone"
                dataKey="revenue"
                stroke={C.primary}
                strokeWidth={2}
                fill="url(#rev)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title={t.dashOrdersTrend}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={trendData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="day" tick={{ fontSize: 11 }} tickLine={false} />
              <YAxis allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} width={28} />
              <Tooltip formatter={(v) => [Number(v), t.dashOrdersUnit]} />
              <Bar dataKey="orders" fill={C.primary} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {!isPro ? (
          <ProLockCard height={264} />
        ) : (
        <ChartCard title={t.dashAnomaly} empty={anomalyData.length === 0}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={anomalyData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="day" tick={{ fontSize: 11 }} tickLine={false} />
              <YAxis tickFormatter={compactLbp} tick={{ fontSize: 11 }} tickLine={false} width={44} />
              <Tooltip formatter={(v) => [formatLbp(Number(v)), t.dashTodayRevenue]} />
              <Bar dataKey="revenue" radius={[4, 4, 0, 0]}>
                {anomalyData.map((d, i) => (
                  <Cell key={i} fill={d.anomalous ? C.destructive : C.muted} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
        )}

        <ChartCard title={t.dashTopProducts} empty={(summary?.top_products ?? []).length === 0}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              layout="vertical"
              data={summary?.top_products ?? []}
              margin={{ top: 8, right: 8, left: 8, bottom: 0 }}
            >
              <CartesianGrid stroke={C.grid} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} />
              <YAxis
                type="category"
                dataKey="name"
                width={catAxisWidth}
                tick={{ fontSize: 12 }}
                tickLine={false}
              />
              <Tooltip formatter={(v) => [Number(v), t.dashUnits]} />
              <Bar dataKey="units" fill={C.accent} radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        {!isPro ? (
          <ProLockCard height={264} />
        ) : (
        <ChartCard title={t.dashDemandForecast} empty={demandData.length === 0}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              layout="vertical"
              data={demandData}
              margin={{ top: 8, right: 8, left: 8, bottom: 0 }}
            >
              <CartesianGrid stroke={C.grid} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} />
              <YAxis
                type="category"
                dataKey="name"
                width={catAxisWidth}
                tick={{ fontSize: 12 }}
                tickLine={false}
              />
              <Tooltip formatter={(v) => [Number(v), t.dashUnits]} />
              <Bar dataKey="units" fill={C.primary} radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
        )}

        <ChartCard title={t.dashInventoryLevels} empty={inventoryData.length === 0}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={inventoryData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
              <CartesianGrid stroke={C.grid} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 10 }} tickLine={false} interval={0} angle={-25} height={56} textAnchor="end" />
              <YAxis allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} width={28} />
              <Tooltip formatter={(v) => [Number(v), t.dashUnits]} />
              <Bar dataKey="quantity" radius={[4, 4, 0, 0]}>
                {inventoryData.map((d, i) => (
                  <Cell key={i} fill={d.is_low ? C.destructive : C.accent} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title={t.dashStatusMix} empty={statusData.length === 0}>
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie
                data={statusData}
                dataKey="value"
                nameKey="name"
                innerRadius={55}
                outerRadius={80}
                paddingAngle={2}
              >
                {statusData.map((_, i) => (
                  <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(v) => [Number(v), t.dashOrdersUnit]} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        {!isPro ? (
          <ProLockCard height={264} />
        ) : (
        <ChartCard title={t.dashChurnTop} empty={churnData.length === 0}>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart
              layout="vertical"
              data={churnData}
              margin={{ top: 8, right: 8, left: 8, bottom: 0 }}
            >
              <CartesianGrid stroke={C.grid} strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} tick={{ fontSize: 11 }} tickLine={false} />
              <YAxis
                type="category"
                dataKey="name"
                width={catAxisWidth}
                tick={{ fontSize: 12 }}
                tickLine={false}
              />
              <Tooltip formatter={(v) => [`${Number(v)}%`, t.dashAtRiskCustomers]} />
              <Bar dataKey="risk" radius={[0, 4, 4, 0]}>
                {churnData.map((d, i) => (
                  <Cell key={i} fill={d.risk >= RISK_THRESHOLD * 100 ? C.destructive : C.amber} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
        )}
      </section>
      )}
    </div>
  );
}

function KpiCard({ label, value, alert }: { label: string; value: string; alert?: boolean }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={`tabular mt-1 truncate text-lg font-semibold ${
          alert ? "text-destructive" : "text-foreground"
        }`}
      >
        {value}
      </p>
    </div>
  );
}

// Charts read left-to-right (dates, numbers), so the inner container is LTR
// while the title stays in the page's RTL flow.
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
