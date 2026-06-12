import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  adminApi,
  getAdminToken,
  setAdminToken,
  type PaymentView,
  type TenantAdminView,
  type TenantDetailAdminView,
} from "../api/admin";
import { ApiError } from "../api/client";
import type { DailyCostResponse } from "../api/types";
import { AdminTabs } from "../components/AdminTabs";
import { Button } from "../components/Button";
import { CostChart } from "../components/CostChart";
import { Field } from "../components/Field";
import { formatDate } from "../format";
import { planLabel, t } from "../i18n";

// Founder tenant directory: every shop on the platform, with a per-tenant
// drill-down (activity counts + 30-day AI cost) and suspend/reactivate.
export default function AdminTenantsPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<TenantAdminView[] | null>(null);
  const [error, setError] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  // No admin token → bounce to the admin login.
  useEffect(() => {
    if (!getAdminToken()) navigate("/admin/login", { replace: true });
  }, [navigate]);

  const reload = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    adminApi
      .listTenants()
      .then((data) => !cancelled && setRows(data))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setAdminToken(null);
          navigate("/admin/login", { replace: true });
        } else if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [tick, navigate]);

  function logout() {
    setAdminToken(null);
    navigate("/admin/login", { replace: true });
  }

  return (
    <main className="min-h-dvh bg-background px-4 py-6">
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        <header className="flex items-center justify-between">
          <h1 className="text-xl font-bold text-primary">{t.tenantsTitle}</h1>
          <button
            onClick={logout}
            className="min-h-[44px] rounded-lg px-3 text-sm font-medium text-destructive hover:bg-destructive/10"
          >
            {t.logout}
          </button>
        </header>

        <AdminTabs active="tenants" />

        {error ? (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
            <p className="text-destructive">{t.tenantsError}</p>
            <Button onClick={reload} className="bg-card !text-foreground ring-1 ring-border">
              {t.retry}
            </Button>
          </div>
        ) : rows === null ? (
          <ul className="flex flex-col gap-3" aria-hidden>
            {[0, 1].map((i) => (
              <li key={i} className="h-24 animate-pulse rounded-2xl border border-border bg-muted/50" />
            ))}
          </ul>
        ) : rows.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center text-muted-foreground">
            {t.tenantsEmpty}
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {rows.map((r) => (
              <TenantCard
                key={r.id}
                tenant={r}
                expanded={expandedId === r.id}
                onToggle={() => setExpandedId(expandedId === r.id ? null : r.id)}
                onChanged={reload}
              />
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}

// Derived billing status — mirrors services/billing.py::effective_subscription_status
// (display concern only; the backend stays the source of truth for actions).
const GRACE_DAYS = 7;

function subscriptionMeta(row: TenantAdminView): { label: string; cls: string } {
  if (!row.is_active)
    return { label: t.subSuspended, cls: "bg-destructive/10 text-destructive" };
  if (!row.current_period_end) {
    // Pro with no paid period = a founder comp (set_plan without a payment) —
    // effectively Pro (plan_gate), so don't show it as a trial.
    if (row.plan_tier === "pro")
      return { label: t.subActive, cls: "bg-accent/15 text-accent" };
    return { label: t.subTrialing, cls: "bg-muted text-muted-foreground" };
  }
  const end = new Date(row.current_period_end);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (end >= today) return { label: t.subActive, cls: "bg-accent/15 text-accent" };
  const daysLate = Math.floor((today.getTime() - end.getTime()) / 86400000);
  if (daysLate <= GRACE_DAYS)
    return { label: t.subPastDue, cls: "bg-status-pending/15 text-status-pending" };
  return { label: t.subExpired, cls: "bg-destructive/10 text-destructive" };
}

function TenantCard({
  tenant,
  expanded,
  onToggle,
  onChanged,
}: {
  tenant: TenantAdminView;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<TenantDetailAdminView | null>(null);
  const [costs, setCosts] = useState<DailyCostResponse | null>(null);
  const [payments, setPayments] = useState<PaymentView[] | null>(null);
  const [detailError, setDetailError] = useState(false);
  const [mode, setMode] = useState<"none" | "action">("none");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Drill-down data is lazy — fetched only when the card is opened.
  useEffect(() => {
    if (!expanded) return;
    let cancelled = false;
    setDetailError(false);
    Promise.all([
      adminApi.tenantDetail(tenant.id),
      adminApi.tenantCosts(tenant.id),
      adminApi.listPayments(tenant.id),
    ])
      .then(([d, c, p]) => {
        if (!cancelled) {
          setDetail(d);
          setCosts(c);
          setPayments(p);
        }
      })
      .catch(() => !cancelled && setDetailError(true));
    return () => {
      cancelled = true;
    };
  }, [expanded, tenant.id]);

  async function doAction() {
    if (!reason.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      if (tenant.is_active) await adminApi.suspendTenant(tenant.id, reason.trim());
      else await adminApi.reactivateTenant(tenant.id, reason.trim());
      setMode("none");
      setReason("");
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 0 ? t.networkError : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  const badge = tenant.is_active
    ? { label: t.tenantActive, cls: "bg-accent/15 text-accent" }
    : { label: t.tenantSuspended, cls: "bg-destructive/10 text-destructive" };

  return (
    <li className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <button onClick={onToggle} className="flex w-full items-start justify-between gap-3 text-start">
        <div className="min-w-0">
          <p className="truncate font-semibold text-foreground">{tenant.name}</p>
          <p className="text-sm text-muted-foreground" dir="ltr">
            {tenant.whatsapp_number}
          </p>
          <p className="tabular text-xs text-muted-foreground">
            {t.tenantPlanLabel}: {tenant.plan_tier} · {formatDate(tenant.created_at)}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
            {badge.label}
          </span>
          <SubBadge tenant={tenant} />
        </div>
      </button>

      {expanded && (
        <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3">
          {detailError ? (
            <p className="text-sm text-destructive">{t.tenantDetailError}</p>
          ) : detail === null ? (
            <div className="h-16 animate-pulse rounded-xl bg-muted/50" aria-hidden />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-xl border border-border bg-background p-3">
                  <p className="text-xs text-muted-foreground">{t.tenantCustomersLabel}</p>
                  <p className="mt-1 text-lg font-semibold text-foreground tabular">
                    {detail.customers_count}
                  </p>
                </div>
                <div className="rounded-xl border border-border bg-background p-3">
                  <p className="text-xs text-muted-foreground">{t.tenantOrdersTodayLabel}</p>
                  <p className="mt-1 text-lg font-semibold text-foreground tabular">
                    {detail.orders_today}
                  </p>
                </div>
              </div>

              {costs && (
                <div className="rounded-xl border border-border bg-background p-3">
                  <p className="mb-2 text-xs text-muted-foreground">{t.tenantCostsTitle}</p>
                  <CostSection costs={costs} />
                </div>
              )}

              <BillingSection
                tenant={tenant}
                payments={payments}
                onChanged={onChanged}
                onPaymentRecorded={(p) =>
                  setPayments((prev) => [p, ...(prev ?? [])])
                }
              />
            </>
          )}

          {mode === "none" && (
            <div>
              <Button
                onClick={() => setMode("action")}
                className={
                  tenant.is_active
                    ? "bg-card !text-destructive ring-1 ring-destructive/40"
                    : undefined
                }
              >
                {tenant.is_active ? t.suspendTenant : t.reactivateTenant}
              </Button>
            </div>
          )}

          {mode === "action" && (
            <div className="flex flex-col gap-3">
              {tenant.is_active && (
                <p className="text-sm text-muted-foreground">{t.suspendWarning}</p>
              )}
              <Field
                name={`reason-${tenant.id}`}
                label={tenant.is_active ? t.suspendReasonPrompt : t.reactivateReasonPrompt}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                disabled={busy}
              />
              {err && <p className="text-sm text-destructive">{err}</p>}
              <div className="flex gap-2">
                <Button
                  onClick={doAction}
                  loading={busy}
                  className={tenant.is_active ? "bg-destructive !text-on-accent" : undefined}
                >
                  {tenant.is_active ? t.suspendConfirm : t.reactivateConfirm}
                </Button>
                <button
                  onClick={() => setMode("none")}
                  className="min-h-[44px] px-3 text-sm text-muted-foreground"
                >
                  {t.cancel}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function CostSection({ costs }: { costs: DailyCostResponse }) {
  if (costs.days.length === 0)
    return <p className="text-center text-sm text-muted-foreground">{t.costsNoData}</p>;
  return <CostChart days={costs.days} budgetUsd={costs.budget_usd} />;
}

function SubBadge({ tenant }: { tenant: TenantAdminView }) {
  const meta = subscriptionMeta(tenant);
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${meta.cls}`}>
      {meta.label}
    </span>
  );
}

// ---- Billing (Phase 11): record a payment, see history, flip the plan ----

const METHODS = [
  { value: "whish", label: t.methodWhish },
  { value: "omt", label: t.methodOmt },
  { value: "cash", label: t.methodCash },
  { value: "card", label: t.methodCard },
  { value: "other", label: t.methodOther },
];

function BillingSection({
  tenant,
  payments,
  onChanged,
  onPaymentRecorded,
}: {
  tenant: TenantAdminView;
  payments: PaymentView[] | null;
  onChanged: () => void;
  onPaymentRecorded: (p: PaymentView) => void;
}) {
  const [mode, setMode] = useState<"none" | "payment" | "edit">("none");
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("whish");
  const [months, setMonths] = useState("1");
  const [note, setNote] = useState("");
  const [planAfter, setPlanAfter] = useState("");
  // Subscription editor (founder override): plan + paid-through, or reset.
  const [planEdit, setPlanEdit] = useState(tenant.plan_tier);
  const [endEdit, setEndEdit] = useState(tenant.current_period_end ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function submitPayment() {
    if (!amount.trim() || Number(amount) <= 0) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await adminApi.recordPayment(tenant.id, {
        amount_usd: amount.trim(),
        method,
        months: Math.max(1, Number(months) || 1),
        note: note.trim() || null,
        plan_tier: planAfter || null,
      });
      onPaymentRecorded(res.payment);
      setMode("none");
      setAmount("");
      setNote("");
      setPlanAfter("");
      setMsg({ ok: true, text: t.paymentRecorded });
      onChanged(); // refresh the directory row (period end + plan)
    } catch {
      setMsg({ ok: false, text: t.paymentRecordError });
    } finally {
      setBusy(false);
    }
  }

  // Founder override: write plan + paid-through directly (or reset to a
  // never-paid state). The backend audits before → after.
  async function submitOverride(plan: string, periodEnd: string | null) {
    setBusy(true);
    setMsg(null);
    try {
      await adminApi.overrideSubscription(tenant.id, {
        plan_tier: plan,
        current_period_end: periodEnd,
      });
      setMode("none");
      setMsg({ ok: true, text: t.subscriptionUpdated });
      onChanged();
    } catch {
      setMsg({ ok: false, text: t.subscriptionUpdateError });
    } finally {
      setBusy(false);
    }
  }

  function resetSubscription() {
    if (!window.confirm(t.resetSubscriptionConfirm)) return;
    void submitOverride("free", null);
  }

  const selectCls =
    "min-h-[44px] rounded-lg border border-border bg-card px-3 text-base text-foreground outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/30";

  return (
    <div className="rounded-xl border border-border bg-background p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">{t.billingSection}</p>
        <SubBadge tenant={tenant} />
      </div>

      {tenant.current_period_end && (
        <p className="mt-1 text-sm text-foreground">
          {t.billingPaidThrough}:{" "}
          <span className="tabular font-medium">{formatDate(tenant.current_period_end)}</span>
        </p>
      )}

      {msg && (
        <p
          role={msg.ok ? "status" : "alert"}
          className={`mt-2 text-sm ${msg.ok ? "text-accent" : "text-destructive"}`}
        >
          {msg.text}
        </p>
      )}

      {mode === "none" && (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button onClick={() => setMode("payment")} disabled={busy} className="text-sm">
            {t.recordPaymentBtn}
          </Button>
          <button
            type="button"
            onClick={() => {
              setPlanEdit(tenant.plan_tier);
              setEndEdit(tenant.current_period_end ?? "");
              setMode("edit");
            }}
            disabled={busy}
            className="min-h-[44px] rounded-lg px-3 text-sm font-medium text-primary hover:bg-primary/10 disabled:opacity-50"
          >
            {t.editSubscription}
          </button>
        </div>
      )}

      {mode === "edit" && (
        <div className="mt-3 flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5 text-start">
              <label
                htmlFor={`sub-plan-${tenant.id}`}
                className="text-sm font-medium text-foreground"
              >
                {t.subPlanLabel}
              </label>
              <select
                id={`sub-plan-${tenant.id}`}
                value={planEdit}
                onChange={(e) => setPlanEdit(e.target.value)}
                disabled={busy}
                className={selectCls}
              >
                <option value="free">{planLabel("free")}</option>
                <option value="pro">{planLabel("pro")}</option>
              </select>
            </div>
            <div className="flex flex-col gap-1.5 text-start">
              <label
                htmlFor={`sub-end-${tenant.id}`}
                className="text-sm font-medium text-foreground"
              >
                {t.subPeriodEndLabel}
              </label>
              <input
                id={`sub-end-${tenant.id}`}
                type="date"
                dir="ltr"
                value={endEdit}
                onChange={(e) => setEndEdit(e.target.value)}
                disabled={busy}
                className={selectCls}
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            {/* Reset = destructive, visually separated from save/cancel. */}
            <button
              type="button"
              onClick={resetSubscription}
              disabled={busy}
              className="min-h-[44px] rounded-lg px-3 text-sm font-medium text-destructive hover:bg-destructive/10 disabled:opacity-50"
            >
              {t.resetSubscription}
            </button>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setMode("none")}
                disabled={busy}
                className="min-h-[44px] rounded-lg px-4 text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                {t.cancel}
              </button>
              <Button
                onClick={() => void submitOverride(planEdit, endEdit || null)}
                loading={busy}
              >
                {t.save}
              </Button>
            </div>
          </div>
        </div>
      )}

      {mode === "payment" && (
        <div className="mt-3 flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3">
            <Field
              name={`amount-${tenant.id}`}
              label={t.paymentAmountUsd}
              type="number"
              inputMode="decimal"
              min={0}
              step="0.01"
              dir="ltr"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              disabled={busy}
            />
            <Field
              name={`months-${tenant.id}`}
              label={t.paymentMonths}
              type="number"
              inputMode="numeric"
              min={1}
              max={24}
              dir="ltr"
              value={months}
              onChange={(e) => setMonths(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5 text-start">
              <label
                htmlFor={`method-${tenant.id}`}
                className="text-sm font-medium text-foreground"
              >
                {t.paymentMethodLabel}
              </label>
              <select
                id={`method-${tenant.id}`}
                value={method}
                onChange={(e) => setMethod(e.target.value)}
                disabled={busy}
                className={selectCls}
              >
                {METHODS.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5 text-start">
              <label
                htmlFor={`plan-${tenant.id}`}
                className="text-sm font-medium text-foreground"
              >
                {t.paymentPlanChange}
              </label>
              <select
                id={`plan-${tenant.id}`}
                value={planAfter}
                onChange={(e) => setPlanAfter(e.target.value)}
                disabled={busy}
                className={selectCls}
              >
                <option value="">{t.paymentPlanKeep}</option>
                <option value="pro">{planLabel("pro")}</option>
                <option value="free">{planLabel("free")}</option>
              </select>
            </div>
          </div>
          <Field
            name={`note-${tenant.id}`}
            label={t.paymentNote}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            disabled={busy}
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setMode("none")}
              disabled={busy}
              className="min-h-[44px] rounded-lg px-4 text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              {t.cancel}
            </button>
            <Button
              onClick={submitPayment}
              loading={busy}
              disabled={!amount.trim() || Number(amount) <= 0}
            >
              {t.recordPaymentBtn}
            </Button>
          </div>
        </div>
      )}

      {/* History */}
      <p className="mt-4 text-xs text-muted-foreground">{t.paymentHistory}</p>
      {payments === null ? (
        <div className="mt-2 h-8 animate-pulse rounded-lg bg-muted/50" aria-hidden />
      ) : payments.length === 0 ? (
        <p className="mt-1 text-sm text-muted-foreground">{t.noPaymentsYet}</p>
      ) : (
        <ul className="mt-1 flex flex-col divide-y divide-border text-sm">
          {payments.map((p) => (
            <li key={p.id} className="flex items-center justify-between gap-2 py-2">
              <span className="min-w-0">
                <span className="tabular font-medium text-foreground" dir="ltr">
                  ${p.amount_usd}
                </span>{" "}
                <span className="text-muted-foreground">
                  · {METHODS.find((m) => m.value === p.method)?.label ?? p.method} ·{" "}
                  <span className="tabular">{p.months}</span> {t.paymentMonths}
                </span>
              </span>
              <span className="tabular shrink-0 text-xs text-muted-foreground">
                {formatDate(p.created_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
