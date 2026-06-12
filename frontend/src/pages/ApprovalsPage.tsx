import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  adminApi,
  getAdminToken,
  setAdminToken,
  type SignupRequestAdminView,
} from "../api/admin";
import { ApiError } from "../api/client";
import { AdminTabs } from "../components/AdminTabs";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { formatDate } from "../format";
import { t } from "../i18n";

function statusLabel(s: string): { label: string; cls: string } {
  if (s === "approved") return { label: t.statusApproved, cls: "bg-accent/15 text-accent" };
  if (s === "rejected")
    return { label: t.statusRejected, cls: "bg-destructive/10 text-destructive" };
  return { label: t.statusPending, cls: "bg-status-pending/15 text-status-pending" };
}

export default function ApprovalsPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<SignupRequestAdminView[] | null>(null);
  const [error, setError] = useState(false);
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
      .listRequests()
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
          <h1 className="text-xl font-bold text-primary">{t.approvalsTitle}</h1>
          <button
            onClick={logout}
            className="min-h-[44px] rounded-lg px-3 text-sm font-medium text-destructive hover:bg-destructive/10"
          >
            {t.logout}
          </button>
        </header>

        <AdminTabs active="approvals" />

        {error ? (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
            <p className="text-destructive">{t.approvalsError}</p>
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
            {t.approvalsEmpty}
          </div>
        ) : (
          <ul className="flex flex-col gap-3">
            {rows.map((r) => (
              <RequestCard key={r.id} req={r} onDone={reload} />
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}

function RequestCard({
  req,
  onDone,
}: {
  req: SignupRequestAdminView;
  onDone: () => void;
}) {
  const [mode, setMode] = useState<"none" | "approve" | "reject">("none");
  // The founder enters the shop's WhatsApp AI number (set up in Meta) before
  // approving — required; the owner never supplied one at signup.
  const [wa, setWa] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const s = statusLabel(req.status);
  const pending = req.status === "pending";

  async function doApprove() {
    if (!wa.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await adminApi.approve(req.id, wa.trim());
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 0 ? t.networkError : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  async function doReject() {
    if (!reason.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await adminApi.reject(req.id, reason.trim());
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 0 ? t.networkError : t.errorGeneric);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold text-foreground">{req.business_name}</p>
          <p className="text-sm text-muted-foreground" dir="ltr">
            {req.owner_email} · {req.owner_phone}
          </p>
          <p className="tabular text-xs text-muted-foreground">{formatDate(req.created_at)}</p>
        </div>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}>
          {s.label}
        </span>
      </div>

      {req.status === "rejected" && req.reject_reason && (
        <p className="mt-2 text-sm text-muted-foreground">— {req.reject_reason}</p>
      )}

      {pending && mode === "none" && (
        <div className="mt-3 flex gap-2">
          <Button onClick={() => setMode("approve")}>{t.approve}</Button>
          <Button
            onClick={() => setMode("reject")}
            className="bg-card !text-destructive ring-1 ring-destructive/40"
          >
            {t.reject}
          </Button>
        </div>
      )}

      {pending && mode === "approve" && (
        <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3">
          <Field
            name={`wa-${req.id}`}
            label={t.approveWaPrompt}
            type="tel"
            inputMode="tel"
            dir="ltr"
            value={wa}
            onChange={(e) => setWa(e.target.value)}
            disabled={busy}
          />
          {err && <p className="text-sm text-destructive">{err}</p>}
          <div className="flex gap-2">
            <Button onClick={doApprove} loading={busy}>
              {t.approveConfirm}
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

      {pending && mode === "reject" && (
        <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3">
          <Field
            name={`reason-${req.id}`}
            label={t.rejectReasonPrompt}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={busy}
          />
          {err && <p className="text-sm text-destructive">{err}</p>}
          <div className="flex gap-2">
            <Button
              onClick={doReject}
              loading={busy}
              className="bg-destructive !text-on-accent"
            >
              {t.rejectConfirm}
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
    </li>
  );
}
