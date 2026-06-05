import { useEffect, useRef } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/context";
import { planLabel, t } from "../i18n";
import { BOTTOM_NAV_ITEMS, NAV_ITEMS } from "../nav";
import { LogoutIcon } from "./icons";
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
          <h1 className="font-sans text-2xl font-bold text-primary">
            {t.appName}
          </h1>
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

        <WhoamiPanel
          businessName={me?.business_name}
          planTier={me?.plan_tier}
          whatsappNumber={me?.whatsapp_number}
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
        <div className="min-w-0">
          <h1 className="font-sans text-lg font-bold text-primary">
            {t.appName}
          </h1>
          <p className="truncate text-xs text-muted-foreground">
            {me?.business_name ?? t.tagline}
          </p>
        </div>
        <button
          onClick={logout}
          aria-label={t.logout}
          className="flex h-11 w-11 items-center justify-center rounded-lg text-destructive transition-colors hover:bg-destructive/10"
        >
          <LogoutIcon className="h-5 w-5" />
        </button>
      </header>

      {/* ----- Content ----- */}
      <main className="px-4 py-5 pb-24 lg:ps-72 lg:pb-8 lg:pe-8">
        <div className="mx-auto max-w-3xl">
          {showSetupBanner && <SetupBanner />}
          <Outlet />
        </div>
      </main>

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
      </nav>
    </div>
  );
}

function WhoamiPanel({
  businessName,
  planTier,
  whatsappNumber,
}: {
  businessName?: string | null;
  planTier?: string;
  whatsappNumber?: string;
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
    </div>
  );
}
