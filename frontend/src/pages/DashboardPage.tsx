import { useAuth } from "../auth/context";
import { Button } from "../components/Button";
import { t } from "../i18n";

// Placeholder home — proves /me loaded and logout works. The real RTL app shell
// (nav, whoami panel, i18n) is Task 3.8; the order feed/customers come after.
export default function DashboardPage() {
  const { me, logout } = useAuth();

  return (
    <main className="min-h-dvh bg-background px-4 py-6">
      <div className="mx-auto flex max-w-md flex-col gap-4">
        <header className="flex items-center justify-between">
          <h1 className="font-sans text-2xl font-bold text-primary">
            {t.appName}
          </h1>
          <Button
            onClick={logout}
            className="bg-card !text-destructive ring-1 ring-border"
          >
            {t.logout}
          </Button>
        </header>

        <section className="rounded-2xl border border-border bg-card p-5 shadow-sm">
          {me ? (
            <dl className="flex flex-col gap-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-muted-foreground">المحل</dt>
                <dd className="font-medium">{me.business_name ?? "—"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">الباقة</dt>
                <dd className="tabular font-medium">{me.plan_tier}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted-foreground">عدد المنتجات</dt>
                <dd className="tabular font-medium">{me.product_count}</dd>
              </div>
            </dl>
          ) : (
            <p className="text-muted-foreground">{t.loading}</p>
          )}
        </section>
      </div>
    </main>
  );
}
