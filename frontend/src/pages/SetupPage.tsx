import { t } from "../i18n";

// Placeholder — the 4-step setup wizard is Task 3.9.
export default function SetupPage() {
  return (
    <div>
      <h2 className="mb-4 text-xl font-bold text-foreground">{t.navSetup}</h2>
      <p className="text-muted-foreground">{t.loading}</p>
    </div>
  );
}
