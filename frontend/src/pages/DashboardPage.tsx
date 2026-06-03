import { useAuth } from "../auth/context";
import { planLabel, t } from "../i18n";

// Home view inside the app shell. The shell provides nav/whoami/logout; this is
// a quick at-a-glance summary. The live order feed lives on /orders (Task 3.11).
export default function DashboardPage() {
  const { me } = useAuth();

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-xl font-bold text-foreground">{t.navHome}</h2>

      {me ? (
        <section className="grid grid-cols-2 gap-3">
          <SummaryCard label={t.shop} value={me.business_name ?? "—"} />
          <SummaryCard label={t.plan} value={planLabel(me.plan_tier)} numeric />
          <SummaryCard
            label={t.productCount}
            value={String(me.product_count)}
            numeric
          />
        </section>
      ) : (
        <p className="text-muted-foreground">{t.loading}</p>
      )}
    </div>
  );
}

function SummaryCard({
  label,
  value,
  numeric,
}: {
  label: string;
  value: string;
  numeric?: boolean;
}) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={`mt-1 truncate text-lg font-semibold text-foreground ${
          numeric ? "tabular" : ""
        }`}
      >
        {value}
      </p>
    </div>
  );
}
