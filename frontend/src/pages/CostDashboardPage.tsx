import { useEffect, useState } from "react";

import { costsApi } from "../api/costs";
import type { DailyCostResponse } from "../api/types";
import { CostChart } from "../components/CostChart";
import { t } from "../i18n";

export default function CostDashboardPage() {
  const [data, setData] = useState<DailyCostResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    costsApi
      .dashboard()
      .then((res) => {
        if (!cancelled) {
          setData(res);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const today = data?.days[data.days.length - 1];
  const todayUsd = today?.total_usd ?? 0;
  const budgetUsd = data?.budget_usd ?? 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-foreground">{t.costsTitle}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t.costsSubtitle}</p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <div className="rounded-xl border border-border bg-card p-4">
          <p className="text-xs text-muted-foreground">{t.costsTodayLabel}</p>
          <p className="mt-1 text-lg font-semibold text-foreground" dir="ltr">
            ${todayUsd.toFixed(4)}
          </p>
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <p className="text-xs text-muted-foreground">{t.costsBudgetLabel}</p>
          <p className="mt-1 text-lg font-semibold text-foreground" dir="ltr">
            {budgetUsd > 0 ? `$${budgetUsd.toFixed(2)}` : t.costsNoBudget}
          </p>
        </div>
        {budgetUsd > 0 && (
          <div className="rounded-xl border border-border bg-card p-4">
            <p className="text-xs text-muted-foreground">%</p>
            <p className="mt-1 text-lg font-semibold text-foreground" dir="ltr">
              {((todayUsd / budgetUsd) * 100).toFixed(1)}%
            </p>
          </div>
        )}
      </div>

      {/* Chart */}
      <div className="rounded-xl border border-border bg-card p-4">
        {loading && (
          <p className="text-center text-sm text-muted-foreground">...</p>
        )}
        {error && (
          <p className="text-center text-sm text-destructive">{t.costsNoData}</p>
        )}
        {data && data.days.length > 0 && (
          <CostChart days={data.days} budgetUsd={budgetUsd} />
        )}
        {data && data.days.length === 0 && (
          <p className="text-center text-sm text-muted-foreground">{t.costsNoData}</p>
        )}
      </div>
    </div>
  );
}
