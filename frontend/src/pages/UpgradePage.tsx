import { useState } from "react";

import { billingApi } from "../api/billing";
import { useAuth } from "../auth/context";
import { Button } from "../components/Button";
import { CheckIcon, CloseIcon, StarIcon } from "../components/icons";
import { formatDate } from "../format";
import { t } from "../i18n";

// Pricing page (Phase 11): Free vs Pro, side by side, with the live Whish
// checkout. The plan difference here mirrors EXACTLY what plan_gate.py
// enforces — if it's promised on this page, the backend gates it.
export default function UpgradePage() {
  const { me } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  const isPro = me?.effective_plan === "pro";
  const price = me?.pro_price_usd ?? 20;
  // Gateway configured → in-app checkout. Otherwise the button IS the Whish
  // pay link: the owner pays there, WhatsApps the receipt, and the founder
  // activates Pro from the admin (manual flow).
  const onlineCheckout = me?.online_checkout_enabled ?? false;
  const whishLink = me?.billing_whish_link ?? "";
  const waReceiptLink = me?.billing_contact_phone
    ? `https://wa.me/${me.billing_contact_phone.replace(/\D/g, "")}?text=${encodeURIComponent(t.receiptPrefill)}`
    : "";

  async function subscribe() {
    setBusy(true);
    setError(false);
    try {
      const { collect_url } = await billingApi.startCheckout(1);
      // Hand off to the Whish-hosted payment page (card or wallet balance).
      window.location.href = collect_url;
    } catch {
      setError(true);
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="text-center">
        <h2 className="text-2xl font-bold text-foreground">{t.upgradeTitle}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t.upgradeSubtitle}</p>
      </div>

      {isPro && (
        <div className="flex items-center justify-center gap-2 rounded-xl border border-accent/30 bg-accent/10 px-4 py-3 text-sm font-medium text-accent">
          <StarIcon className="h-4 w-4" />
          {t.youArePro}
          {me?.current_period_end && (
            <span className="tabular">
              ({t.billingPaidThrough}: {formatDate(me.current_period_end)})
            </span>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {/* ---- Free ---- */}
        <div className="flex flex-col rounded-2xl border border-border bg-card p-5">
          <h3 className="text-lg font-bold text-foreground">{t.planFree}</h3>
          <p className="mt-1">
            <span className="tabular text-3xl font-bold text-foreground" dir="ltr">
              $0
            </span>
            <span className="text-sm text-muted-foreground"> {t.perMonth}</span>
          </p>
          <ul className="mt-4 flex flex-1 flex-col gap-2.5 text-sm">
            <Feat ok label={t.featOrdersBoth} />
            <Feat ok label={t.featProductsFree} />
            <Feat ok label={t.featBillsFree} />
            <Feat ok label={t.featChatFree} />
            <Feat label={t.featInsightsFree} />
            <Feat label={t.featVoiceFree} />
          </ul>
          {!isPro && (
            <p className="mt-4 text-center text-xs font-medium text-muted-foreground">
              {t.currentPlanBadge}
            </p>
          )}
        </div>

        {/* ---- Pro ---- */}
        <div className="relative flex flex-col rounded-2xl border-2 border-primary bg-card p-5 shadow-lg">
          <span className="absolute -top-3 start-4 inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1 text-xs font-bold text-on-primary">
            <StarIcon className="h-3.5 w-3.5" />
            {t.bestValue}
          </span>
          <h3 className="text-lg font-bold text-primary">{t.planPro}</h3>
          <p className="mt-1">
            <span className="tabular text-3xl font-bold text-foreground" dir="ltr">
              ${price}
            </span>
            <span className="text-sm text-muted-foreground"> {t.perMonth}</span>
          </p>
          <ul className="mt-4 flex flex-1 flex-col gap-2.5 text-sm">
            <Feat ok label={t.featOrdersBoth} />
            <Feat ok label={t.featProductsPro} />
            <Feat ok label={t.featBillsPro} />
            <Feat ok label={t.featChatPro} />
            <Feat ok label={t.featInsightsPro} />
            <Feat ok label={t.featVoicePro} />
          </ul>
          {isPro ? (
            <p className="mt-4 text-center text-xs font-medium text-accent">
              {t.currentPlanBadge}
            </p>
          ) : onlineCheckout ? (
            <Button onClick={subscribe} loading={busy} className="mt-4 w-full">
              {busy ? t.payingRedirect : t.subscribeNow}
            </Button>
          ) : whishLink ? (
            // Manual flow: the subscribe button takes the owner to the Whish
            // payment page; activation happens after the founder confirms.
            <>
              <a
                href={whishLink}
                target="_blank"
                rel="noreferrer"
                className="mt-4 flex min-h-[44px] w-full items-center justify-center rounded-lg bg-primary px-4 font-medium text-on-primary transition-[opacity,transform] hover:opacity-90 active:scale-[0.98]"
              >
                {t.subscribeViaWhish}
              </a>
              <p className="mt-2 text-center text-xs text-muted-foreground">
                {t.manualActivationHint}
              </p>
              {waReceiptLink && (
                <a
                  href={waReceiptLink}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 flex min-h-[44px] items-center justify-center text-sm font-medium text-primary underline"
                >
                  {t.sendReceiptWhatsapp}
                </a>
              )}
            </>
          ) : waReceiptLink ? (
            // No pay link configured: at least open the WhatsApp conversation.
            <a
              href={waReceiptLink}
              target="_blank"
              rel="noreferrer"
              className="mt-4 flex min-h-[44px] w-full items-center justify-center rounded-lg bg-primary px-4 font-medium text-on-primary transition-[opacity,transform] hover:opacity-90 active:scale-[0.98]"
            >
              {t.renewContactCta}
            </a>
          ) : null}
        </div>
      </div>

      {error && (
        <p role="alert" className="text-center text-sm text-destructive">
          {t.checkoutError}
        </p>
      )}
    </div>
  );
}

function Feat({ ok, label }: { ok?: boolean; label: string }) {
  return (
    <li className="flex items-center gap-2">
      {ok ? (
        <CheckIcon className="h-4 w-4 shrink-0 text-accent" />
      ) : (
        <CloseIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
      )}
      <span className={ok ? "text-foreground" : "text-muted-foreground"}>{label}</span>
    </li>
  );
}
