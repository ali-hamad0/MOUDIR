import { Link } from "react-router-dom";

import { t } from "../i18n";
import { SetupIcon } from "./icons";

// Persistent reminder shown when the shop setup is incomplete and the owner is
// not already on the wizard. Not dismissible — an unconfigured shop can't take
// orders, so the nudge stays until setup_complete flips true.
export function SetupBanner() {
  return (
    <div
      role="status"
      className="mb-4 flex flex-col gap-3 rounded-xl border border-primary/30 bg-primary/10 p-4 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-start gap-3">
        <SetupIcon className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
        <div>
          <p className="font-semibold text-foreground">
            {t.setupIncompleteTitle}
          </p>
          <p className="text-sm text-muted-foreground">
            {t.setupIncompleteBody}
          </p>
        </div>
      </div>
      <Link
        to="/setup"
        className="inline-flex min-h-[44px] shrink-0 items-center justify-center rounded-lg bg-primary px-4 text-sm font-medium text-on-primary transition-opacity hover:opacity-90"
      >
        {t.setupIncompleteCta}
      </Link>
    </div>
  );
}
