import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import { customersApi } from "../api/customers";
import { predictionsApi } from "../api/predictions";
import { profileApi } from "../api/profile";
import type {
  AnomalyPredictions,
  ChurnPredictions,
  DemandPredictions,
} from "../api/types";
import { Button } from "../components/Button";
import { AlertIcon } from "../components/icons";
import { formatDate, formatLbp } from "../format";
import { t } from "../i18n";

// Read-only ML "Insights" panel (Phase 6, Task 6.13). Renders the three predictions
// for the logged-in tenant — demand per product, at-risk customers, daily revenue
// anomalies — RTL/Arabic, functional (not polished). Values are null under the stub
// (ml_mode=stub); we still list the tenant's entities and show a note.

interface Data {
  demand: DemandPredictions;
  churn: ChurnPredictions;
  anomaly: AnomalyPredictions;
  productNames: Map<string, string>;
  customerNames: Map<string, string>;
}

export default function InsightsPage() {
  const [data, setData] = useState<Data | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    Promise.all([
      predictionsApi.demand(),
      predictionsApi.churn(),
      predictionsApi.anomaly(),
      profileApi.listProducts(),
      customersApi.list(100, 0),
    ])
      .then(([demand, churn, anomaly, products, customers]) => {
        if (cancelled) return;
        const productNames = new Map(products.map((p) => [p.id, p.name_ar]));
        const customerNames = new Map(
          customers.items.map((c) => [c.id, c.display_name || t.unknownCustomer]),
        );
        setData({ demand, churn, anomaly, productNames, customerNames });
      })
      .catch((err) => {
        if (!cancelled) setError(!(err instanceof ApiError && err.status === 401));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [reloadTick]);

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-xl font-bold text-foreground">{t.insightsTitle}</h2>

      {loading ? (
        <Skeletons />
      ) : error || !data ? (
        <ErrorState onRetry={() => setReloadTick((n) => n + 1)} />
      ) : (
        <>
          {data.demand.mode === "stub" && (
            <p className="rounded-xl border border-status-pending/30 bg-status-pending/10 p-3 text-sm text-foreground">
              {t.insightsStubNote}
            </p>
          )}

          <DemandSection data={data} />
          <ChurnSection data={data} />
          <AnomalySection data={data} />
        </>
      )}
    </div>
  );
}

function Section({
  title,
  asOf,
  empty,
  children,
}: {
  title: string;
  asOf: string;
  empty: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h3 className="font-semibold text-foreground">{title}</h3>
        <span className="tabular text-xs text-muted-foreground">
          {t.asOfLabel} {formatDate(asOf)}
        </span>
      </div>
      {empty ? (
        <p className="py-2 text-sm text-muted-foreground">{children}</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">{children}</ul>
      )}
    </section>
  );
}

function Row({ label, sub, value }: { label: string; sub?: string; value: React.ReactNode }) {
  return (
    <li className="flex items-center justify-between gap-3 py-2">
      <div className="min-w-0">
        <p className="truncate text-sm text-foreground">{label}</p>
        {sub && (
          <p className="tabular text-xs text-muted-foreground" dir="ltr">
            {sub}
          </p>
        )}
      </div>
      {value}
    </li>
  );
}

function DemandSection({ data }: { data: Data }) {
  // Highest forecast first; products with no signal (null) sink to the bottom.
  const items = [...data.demand.items].sort(
    (a, b) => (b.predicted_units ?? -1) - (a.predicted_units ?? -1),
  );
  return (
    <Section title={t.insightsDemandTitle} asOf={data.demand.as_of} empty={items.length === 0}>
      {items.length === 0
        ? t.insightsDemandEmpty
        : items.map((it) => (
            <Row
              key={it.product_id}
              label={data.productNames.get(it.product_id) ?? t.colProduct}
              value={
                <span className="tabular text-sm font-medium text-foreground">
                  {it.predicted_units ?? "—"}
                </span>
              }
            />
          ))}
    </Section>
  );
}

function ChurnSection({ data }: { data: Data }) {
  // Most at-risk first; show the top 10 (a focused worklist, not the whole book).
  const items = [...data.churn.items]
    .sort((a, b) => (b.risk ?? -1) - (a.risk ?? -1))
    .slice(0, 10);
  return (
    <Section title={t.insightsChurnTitle} asOf={data.churn.as_of} empty={items.length === 0}>
      {items.length === 0
        ? t.insightsChurnEmpty
        : items.map((it) => (
            <Row
              key={it.customer_id}
              label={data.customerNames.get(it.customer_id) ?? t.unknownCustomer}
              value={<RiskBadge risk={it.risk} />}
            />
          ))}
    </Section>
  );
}

function AnomalySection({ data }: { data: Data }) {
  // Most recent day first.
  const items = [...data.anomaly.items].reverse();
  return (
    <Section title={t.insightsAnomalyTitle} asOf={data.anomaly.as_of} empty={items.length === 0}>
      {items.length === 0
        ? t.insightsAnomalyEmpty
        : items.map((it) => (
            <Row
              key={it.day}
              label={formatDate(it.day)}
              sub={formatLbp(it.revenue_lbp)}
              value={<AnomalyBadge flag={it.is_anomalous} />}
            />
          ))}
    </Section>
  );
}

function RiskBadge({ risk }: { risk: number | null }) {
  if (risk == null) return <span className="text-sm text-muted-foreground">—</span>;
  const pct = Math.round(risk * 100);
  const high = risk >= 0.5;
  const cls = high
    ? "bg-destructive/10 text-destructive"
    : "bg-muted text-muted-foreground";
  return (
    <span className={`tabular rounded-full px-2.5 py-1 text-xs font-medium ${cls}`}>{pct}%</span>
  );
}

function AnomalyBadge({ flag }: { flag: boolean | null }) {
  if (flag == null) return <span className="text-sm text-muted-foreground">—</span>;
  if (!flag)
    return (
      <span className="rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
        {t.anomalyNormal}
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-2.5 py-1 text-xs font-medium text-destructive">
      <AlertIcon width={14} height={14} />
      {t.anomalyFlag}
    </span>
  );
}

function Skeletons() {
  return (
    <div className="flex flex-col gap-4" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-40 animate-pulse rounded-2xl border border-border bg-muted/50" />
      ))}
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
      <p className="text-destructive">{t.insightsError}</p>
      <Button onClick={onRetry} className="bg-card !text-foreground ring-1 ring-border">
        {t.retry}
      </Button>
    </div>
  );
}
