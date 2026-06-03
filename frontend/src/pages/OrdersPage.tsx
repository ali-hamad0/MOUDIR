import { t } from "../i18n";

// Placeholder — the live order feed (polling, dual currency, states) is Task 3.11.
export default function OrdersPage() {
  return (
    <div>
      <h2 className="mb-4 text-xl font-bold text-foreground">{t.navOrders}</h2>
      <p className="text-muted-foreground">{t.loading}</p>
    </div>
  );
}
