import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { billingApi } from "../api/billing";
import { useAuth } from "../auth/context";
import { CheckIcon, CloseIcon } from "../components/icons";
import { t } from "../i18n";

const POLL_MS = 2500;
const MAX_POLLS = 12; // ~30s, then show the "still confirming" message

// Whish redirects here after the hosted payment page. We NEVER trust the
// redirect itself — this page polls the backend, which verifies with Whish
// server-side and only then activates Pro.
export default function BillingResultPage() {
  const [params] = useSearchParams();
  const { refreshMe } = useAuth();
  const checkoutId = params.get("checkout");
  const [state, setState] = useState<"checking" | "paid" | "failed" | "pending">(
    "checking",
  );
  const polls = useRef(0);

  useEffect(() => {
    if (!checkoutId) {
      setState("failed");
      return;
    }
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      if (cancelled) return;
      try {
        const res = await billingApi.checkoutStatus(checkoutId as string);
        if (cancelled) return;
        if (res.status === "paid") {
          setState("paid");
          void refreshMe(); // refetch /me so locks lift immediately
          return;
        }
        if (res.status === "failed") {
          setState("failed");
          return;
        }
      } catch {
        /* transient — keep polling */
      }
      polls.current += 1;
      if (polls.current >= MAX_POLLS) {
        setState("pending");
        return;
      }
      timer = window.setTimeout(poll, POLL_MS);
    }
    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkoutId]);

  return (
    <div className="flex flex-col items-center gap-4 rounded-2xl border border-border bg-card p-8 text-center">
      {state === "checking" && (
        <>
          <span
            aria-hidden
            className="h-10 w-10 animate-spin rounded-full border-4 border-primary/30 border-t-primary"
          />
          <p className="font-medium text-foreground">{t.billingResultChecking}</p>
        </>
      )}

      {state === "paid" && (
        <>
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-accent/15 text-accent">
            <CheckIcon className="h-6 w-6" />
          </span>
          <p className="text-lg font-bold text-foreground">{t.billingResultSuccess}</p>
        </>
      )}

      {state === "failed" && (
        <>
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
            <CloseIcon className="h-6 w-6" />
          </span>
          <p className="text-lg font-bold text-foreground">{t.billingResultFailed}</p>
          <Link to="/upgrade" className="font-medium text-primary underline">
            {t.retry}
          </Link>
        </>
      )}

      {state === "pending" && (
        <p className="font-medium text-foreground">{t.billingResultPending}</p>
      )}

      <Link
        to="/"
        className="mt-2 flex min-h-[44px] items-center rounded-lg px-4 text-sm font-medium text-muted-foreground hover:text-foreground"
      >
        {t.backToHome}
      </Link>
    </div>
  );
}
