import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/context";
import { lang, planLabel, setLang, t } from "../i18n";
import { BOTTOM_NAV_ITEMS, BOTTOM_NAV_MORE_ITEMS, NAV_ITEMS } from "../nav";
import { CloseIcon, GlobeIcon, LogoutIcon, MoreIcon } from "./icons";
import { AIStatusBanner } from "./AIStatusBanner";
import { SetupBanner } from "./SetupBanner";

const FIRST_LOGIN_REDIRECT_KEY = "modir.wizardRedirected";

// Mobile-first RTL shell: a sidebar at ≥lg (1024px), a bottom nav on phones
// (ux adaptive-navigation). The active route is highlighted; logout is visually
// separated from primary nav (ux destructive-nav-separation). Copy comes from
// the i18n dictionary, never inline literals.
export function AppShell() {
  const { me, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const redirected = useRef(false);
  // Mobile "More" sheet: the bottom nav holds the 4 day-to-day pages + this
  // sheet with the rest, so every destination stays reachable on a phone.
  const [moreOpen, setMoreOpen] = useState(false);

  // Navigating (from the sheet or anywhere else) dismisses the sheet.
  useEffect(() => {
    setMoreOpen(false);
  }, [location.pathname]);

  // The More tab lights up when the current page lives inside the sheet.
  const moreActive = BOTTOM_NAV_MORE_ITEMS.some(({ to }) =>
    location.pathname.startsWith(to),
  );

  // First login with an incomplete shop → launch the wizard automatically, but
  // only ONCE per session (sessionStorage-flagged). After that the owner can
  // navigate freely; the persistent banner keeps nudging until setup is done.
  useEffect(() => {
    if (!me || me.setup_complete) return;
    if (redirected.current) return;
    if (sessionStorage.getItem(FIRST_LOGIN_REDIRECT_KEY)) return;
    if (location.pathname === "/setup") return;
    redirected.current = true;
    sessionStorage.setItem(FIRST_LOGIN_REDIRECT_KEY, "1");
    navigate("/setup", { replace: true });
  }, [me, location.pathname, navigate]);

  const showSetupBanner =
    me != null && !me.setup_complete && location.pathname !== "/setup";

  return (
    <div className="min-h-dvh bg-background text-foreground">
      {/* ----- Sidebar (desktop ≥ lg) ----- */}
      <aside className="fixed inset-y-0 start-0 z-20 hidden w-64 flex-col border-e border-border bg-card px-4 py-6 lg:flex">
        <div className="mb-8 px-2">
          <div className="flex items-center gap-2">
            <img
              src="/logo.jpg"
              alt={t.appName}
              className="h-9 w-9 shrink-0 rounded-lg object-contain"
            />
            <h1 className="font-sans text-2xl font-bold text-primary">
              {t.appName}
            </h1>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{t.tagline}</p>
        </div>

        <nav className="flex flex-1 flex-col gap-1">
          {NAV_ITEMS.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `flex min-h-[44px] items-center gap-3 rounded-lg px-3 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`
              }
            >
              <Icon className="h-5 w-5" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <LangToggle className="mb-3 flex min-h-[44px] w-full items-center gap-3 rounded-lg px-3 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground" />
        <WhoamiPanel
          businessName={me?.business_name}
          planTier={me?.plan_tier}
          whatsappNumber={me?.whatsapp_number}
          periodEnd={me?.current_period_end}
        />
        <button
          onClick={logout}
          className="mt-3 flex min-h-[44px] items-center gap-3 rounded-lg px-3 text-sm font-medium text-destructive transition-colors hover:bg-destructive/10"
        >
          <LogoutIcon className="h-5 w-5" />
          <span>{t.logout}</span>
        </button>
      </aside>

      {/* ----- Mobile top bar (whoami + logout) ----- */}
      <header className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-card px-4 py-3 lg:hidden">
        <div className="flex min-w-0 items-center gap-2">
          <img
            src="/logo.jpg"
            alt={t.appName}
            className="h-9 w-9 shrink-0 rounded-lg object-contain"
          />
          <div className="min-w-0">
            <h1 className="font-sans text-lg font-bold text-primary">
              {t.appName}
            </h1>
            <p className="truncate text-xs text-muted-foreground">
              {me?.business_name ?? t.tagline}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <LangToggle className="flex h-11 items-center gap-2 rounded-lg px-3 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground" />
          <button
            onClick={logout}
            aria-label={t.logout}
            className="flex h-11 w-11 items-center justify-center rounded-lg text-destructive transition-colors hover:bg-destructive/10"
          >
            <LogoutIcon className="h-5 w-5" />
          </button>
        </div>
      </header>

      {/* ----- Content ----- */}
      <main className="px-4 py-5 pb-24 lg:ps-72 lg:pb-8 lg:pe-8">
        <div className="mx-auto max-w-3xl">
          {showSetupBanner && <SetupBanner />}
          {me && <BillingBanner me={me} />}
          <AIStatusBanner />
          <Outlet />
        </div>
      </main>

      {/* ----- "More" sheet (mobile) — the rest of the sidebar destinations ----- */}
      {moreOpen && (
        <div
          className="fixed inset-0 z-30 flex items-end bg-foreground/40 lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label={t.navMore}
          onClick={() => setMoreOpen(false)}
        >
          <div
            className="w-full rounded-t-2xl border-t border-border bg-card p-4 pb-[max(1rem,env(safe-area-inset-bottom))] shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 flex items-center justify-between">
              <p className="text-sm font-semibold text-muted-foreground">
                {t.navMore}
              </p>
              <button
                type="button"
                onClick={() => setMoreOpen(false)}
                aria-label={t.close}
                className="flex h-11 w-11 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted"
              >
                <CloseIcon className="h-5 w-5" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {BOTTOM_NAV_MORE_ITEMS.map(({ to, label, Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) =>
                    `flex min-h-[48px] items-center gap-3 rounded-xl px-3 text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-primary/10 text-primary"
                        : "text-foreground hover:bg-muted"
                    }`
                  }
                >
                  <Icon className="h-5 w-5 shrink-0" />
                  <span className="truncate">{label}</span>
                </NavLink>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ----- Bottom nav (mobile) ----- */}
      <nav className="fixed inset-x-0 bottom-0 z-20 flex border-t border-border bg-card pb-[env(safe-area-inset-bottom)] lg:hidden">
        {BOTTOM_NAV_ITEMS.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `flex flex-1 flex-col items-center justify-center gap-1 py-2 text-xs font-medium transition-colors ${
                isActive ? "text-primary" : "text-muted-foreground"
              }`
            }
          >
            <Icon className="h-6 w-6" />
            <span>{label}</span>
          </NavLink>
        ))}
        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={moreOpen}
          className={`flex flex-1 flex-col items-center justify-center gap-1 py-2 text-xs font-medium transition-colors ${
            moreActive || moreOpen ? "text-primary" : "text-muted-foreground"
          }`}
        >
          <MoreIcon className="h-6 w-6" />
          <span>{t.navMore}</span>
        </button>
      </nav>
    </div>
  );
}

// Switch the dashboard language (Arabic ⟷ English). The label always shows the
// *other* language; setLang persists the choice and reloads so every string
// (including module-level ones like NAV_ITEMS) re-resolves.
function LangToggle({ className }: { className: string }) {
  return (
    <button onClick={() => setLang(lang === "ar" ? "en" : "ar")} className={className}>
      <GlobeIcon className="h-5 w-5" />
      <span>{t.langToggle}</span>
    </button>
  );
}

function WhoamiPanel({
  businessName,
  planTier,
  whatsappNumber,
  periodEnd,
}: {
  businessName?: string | null;
  planTier?: string;
  whatsappNumber?: string;
  periodEnd?: string | null;
}) {
  return (
    <div className="rounded-xl border border-border bg-background p-3 text-sm">
      <p className="text-xs text-muted-foreground">{t.shop}</p>
      <p className="truncate font-semibold text-foreground">
        {businessName ?? "—"}
      </p>
      {whatsappNumber && (
        <p className="mt-1 text-xs text-muted-foreground">
          {t.whatsappNumberLabel}:{" "}
          <span className="tabular" dir="ltr">
            {whatsappNumber}
          </span>
        </p>
      )}
      {planTier && (
        <p className="mt-1 text-xs text-muted-foreground">
          {t.plan}: <span className="tabular">{planLabel(planTier)}</span>
        </p>
      )}
      {periodEnd && (
        <p className="mt-1 text-xs text-muted-foreground">
          {t.billingPaidThrough}:{" "}
          <span className="tabular">{new Date(periodEnd).toLocaleDateString("en-GB")}</span>
        </p>
      )}
    </div>
  );
}

// Renewal nudge (Phase 11): shows only when the backend-derived status says
// the paid period lapsed. CTAs come from settings via /me — a Whish pay link
// and/or the founder's WhatsApp; absent values hide the button.
function BillingBanner({
  me,
}: {
  me: {
    subscription_status: string;
    billing_whish_link: string;
    billing_contact_phone: string;
  };
}) {
  const past = me.subscription_status === "past_due";
  const expired = me.subscription_status === "expired";
  if (!past && !expired) return null;

  const waLink = me.billing_contact_phone
    ? `https://wa.me/${me.billing_contact_phone.replace(/\D/g, "")}`
    : "";

  return (
    <div
      role="alert"
      className={`mb-4 flex flex-col gap-2 rounded-xl border px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between ${
        expired
          ? "border-destructive/40 bg-destructive/10 text-destructive"
          : "border-status-pending/40 bg-status-pending/10 text-status-pending"
      }`}
    >
      <p className="font-medium">
        {expired ? t.billingExpiredBanner : t.billingPastDueBanner}
      </p>
      <div className="flex shrink-0 gap-2">
        {me.billing_whish_link && (
          <a
            href={me.billing_whish_link}
            target="_blank"
            rel="noreferrer"
            className="flex min-h-[44px] items-center rounded-lg bg-primary px-3 text-sm font-medium text-on-primary hover:opacity-90"
          >
            {t.renewWhishCta}
          </a>
        )}
        {waLink && (
          <a
            href={waLink}
            target="_blank"
            rel="noreferrer"
            className="flex min-h-[44px] items-center rounded-lg px-3 text-sm font-medium underline"
          >
            {t.renewContactCta}
          </a>
        )}
      </div>
    </div>
  );
}
