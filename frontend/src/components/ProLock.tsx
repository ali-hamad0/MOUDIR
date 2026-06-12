import { Link } from "react-router-dom";

import { t } from "../i18n";
import { LockIcon } from "./icons";

// Locked-feature card (Phase 11): shown wherever a Free tenant meets a
// Pro-only feature. The backend enforces the gate (402); this is the friendly
// face of it — always with a path to /upgrade, never a dead end.
export function ProLockCard({ height }: { height?: number }) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-border bg-card p-6 text-center"
      style={height ? { minHeight: height } : undefined}
    >
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
        <LockIcon className="h-5 w-5" />
      </span>
      <p className="text-sm font-semibold text-foreground">{t.proFeatureLock}</p>
      <p className="text-xs text-muted-foreground">{t.proFeatureLockHint}</p>
      <Link
        to="/upgrade"
        className="mt-1 flex min-h-[44px] items-center rounded-lg bg-primary px-4 text-sm font-medium text-on-primary transition-[opacity,transform] hover:opacity-90 active:scale-[0.98]"
      >
        {t.upgradeCta}
      </Link>
    </div>
  );
}

// Inline variant for forms/toasts: message + upgrade link in one line.
export function ProLimitNotice({ message }: { message: string }) {
  return (
    <p role="alert" className="text-sm text-status-pending">
      {message}{" "}
      <Link to="/upgrade" className="font-medium text-primary underline">
        {t.upgradeCta}
      </Link>
    </p>
  );
}
