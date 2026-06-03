import { t } from "../i18n";

// Placeholder — the customers list view is Task 3.12.
export default function CustomersPage() {
  return (
    <div>
      <h2 className="mb-4 text-xl font-bold text-foreground">
        {t.navCustomers}
      </h2>
      <p className="text-muted-foreground">{t.loading}</p>
    </div>
  );
}
