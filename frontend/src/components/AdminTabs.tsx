import { Link } from "react-router-dom";

import { t } from "../i18n";

// Founder admin nav: platform overview ↔ signup approvals ↔ tenant directory.
export function AdminTabs({
  active,
}: {
  active: "overview" | "approvals" | "tenants";
}) {
  const base =
    "min-h-[44px] flex flex-1 items-center justify-center rounded-lg px-3 text-sm font-medium transition-colors sm:flex-none sm:px-4";
  const on = "bg-primary text-on-accent";
  const off = "text-muted-foreground hover:bg-muted";
  return (
    <nav className="flex gap-2 rounded-xl border border-border bg-card p-1">
      <Link to="/admin/overview" className={`${base} ${active === "overview" ? on : off}`}>
        {t.overviewTab}
      </Link>
      <Link to="/admin" className={`${base} ${active === "approvals" ? on : off}`}>
        {t.approvalsTab}
      </Link>
      <Link to="/admin/tenants" className={`${base} ${active === "tenants" ? on : off}`}>
        {t.tenantsTab}
      </Link>
    </nav>
  );
}
