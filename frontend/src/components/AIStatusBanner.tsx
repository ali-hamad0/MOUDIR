import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { fetchAIHealth } from "../api/health";
import { t } from "../i18n";

const POLL_INTERVAL_MS = 30_000;

export function AIStatusBanner() {
  const [available, setAvailable] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function check() {
      const result = await fetchAIHealth();
      if (!cancelled) setAvailable(result.available);
    }

    check();
    const id = setInterval(check, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (available) return null;

  return (
    <div
      role="status"
      className="mb-4 flex flex-col gap-3 rounded-xl border border-destructive/30 bg-destructive/10 p-4 sm:flex-row sm:items-center sm:justify-between"
    >
      <div>
        <p className="font-semibold text-destructive">{t.aiBannerTitle}</p>
        <p className="text-sm text-muted-foreground">{t.aiBannerBody}</p>
      </div>
      <Link
        to="/orders/manual"
        className="inline-flex min-h-[44px] shrink-0 items-center justify-center rounded-lg bg-destructive px-4 text-sm font-medium text-white transition-opacity hover:opacity-90"
      >
        {t.aiBannerCta}
      </Link>
    </div>
  );
}
