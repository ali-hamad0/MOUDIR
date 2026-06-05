import { useCallback, useMemo, useState } from "react";

import { ApiError } from "../api/client";
import { reordersApi } from "../api/reorders";
import type { ApprovalRead } from "../api/types";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Toast, type ToastState } from "../components/Toast";
import { formatDate } from "../format";
import { usePolling } from "../hooks/usePolling";
import { poStatusMeta, t } from "../i18n";

// Poll so a just-approved PO flips draft → approved → sent (the background
// dispatch lands within a few seconds) without the owner refreshing.
const POLL_MS = 5000;

export default function ReordersPage() {
  const fetcher = useCallback(() => reordersApi.list(), []);
  const { data, loading, error, refetch } = usePolling(fetcher, POLL_MS);
  const [toast, setToast] = useState<ToastState | null>(null);

  // The inbox returns both pending drafts and the dispatch_failed manual queue;
  // split them so each gets its own section + actions.
  const { pending, manual } = useMemo(() => {
    const items = data?.items ?? [];
    return {
      pending: items.filter((p) => p.status === "draft"),
      manual: items.filter((p) => p.status === "dispatch_failed"),
    };
  }, [data]);

  function onError(message: string) {
    setToast({ kind: "error", message });
  }
  function onSuccess(message: string) {
    setToast({ kind: "success", message });
    refetch();
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-xl font-bold text-foreground">{t.reordersTitle}</h2>

      {loading ? (
        <Skeletons />
      ) : error && !data ? (
        <ErrorState onRetry={refetch} />
      ) : pending.length === 0 && manual.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="flex flex-col gap-6">
          {pending.length > 0 && (
            <ul className="flex flex-col gap-3">
              {pending.map((po) => (
                <PendingCard key={po.id} po={po} onSuccess={onSuccess} onError={onError} />
              ))}
            </ul>
          )}

          {manual.length > 0 && (
            <section className="flex flex-col gap-3">
              <h3 className="text-sm font-semibold text-destructive">{t.manualQueueTitle}</h3>
              <ul className="flex flex-col gap-3">
                {manual.map((po) => (
                  <ManualCard key={po.id} po={po} onSuccess={onSuccess} onError={onError} />
                ))}
              </ul>
            </section>
          )}
        </div>
      )}

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}

// ---- Shared bits ----

function StatusBadge({ status }: { status: string }) {
  const s = poStatusMeta(status);
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {s.label}
    </span>
  );
}

function POHeader({ po }: { po: ApprovalRead }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="truncate font-semibold text-foreground">{po.product_name_ar}</p>
        <p className="text-xs text-muted-foreground">
          {t.poSupplier}: {po.supplier_name || t.poNoSupplier}
        </p>
        <p className="tabular text-xs text-muted-foreground">{formatDate(po.created_at)}</p>
      </div>
      <StatusBadge status={po.status} />
    </div>
  );
}

function POBody({ po }: { po: ApprovalRead }) {
  return (
    <dl className="mt-3 flex flex-col gap-2 border-t border-border pt-3 text-sm">
      <div className="flex justify-between gap-2">
        <dt className="text-muted-foreground">{t.poQuantity}</dt>
        <dd className="tabular font-medium text-foreground">{po.quantity}</dd>
      </div>
      {po.agent_note_ar && (
        <div className="flex flex-col gap-0.5">
          <dt className="text-muted-foreground">{t.poAgentNote}</dt>
          <dd className="text-foreground">{po.agent_note_ar}</dd>
        </div>
      )}
    </dl>
  );
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError && e.status === 0) return t.networkError;
  return t.poActionError;
}

// ---- Pending draft: approve (optional note) / reject (reason required) ----

function PendingCard({
  po,
  onSuccess,
  onError,
}: {
  po: ApprovalRead;
  onSuccess: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [mode, setMode] = useState<"none" | "approve" | "reject">("none");
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function doApprove() {
    setBusy(true);
    try {
      await reordersApi.approve(po.id, note.trim() || undefined);
      onSuccess(t.poApproved);
    } catch (e) {
      onError(errMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function doReject() {
    if (!reason.trim()) {
      setReasonError(t.poRejectReasonRequired);
      return;
    }
    setBusy(true);
    try {
      await reordersApi.reject(po.id, reason.trim());
      onSuccess(t.poRejected);
    } catch (e) {
      onError(errMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <POHeader po={po} />
      <POBody po={po} />

      {mode === "none" && (
        <div className="mt-3 flex gap-2">
          <Button onClick={() => setMode("approve")}>{t.poApprove}</Button>
          <Button
            onClick={() => setMode("reject")}
            className="bg-card !text-destructive ring-1 ring-destructive/40"
          >
            {t.poReject}
          </Button>
        </div>
      )}

      {mode === "approve" && (
        <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3">
          <Field
            name={`note-${po.id}`}
            label={t.poApproveNoteLabel}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            disabled={busy}
          />
          <div className="flex gap-2">
            <Button onClick={doApprove} loading={busy}>
              {t.poApproveConfirm}
            </Button>
            <CancelButton onClick={() => setMode("none")} disabled={busy} />
          </div>
        </div>
      )}

      {mode === "reject" && (
        <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3">
          <Field
            name={`reason-${po.id}`}
            label={t.poRejectReasonLabel}
            value={reason}
            error={reasonError}
            onChange={(e) => {
              setReason(e.target.value);
              if (reasonError) setReasonError(null);
            }}
            onBlur={() => setReasonError(reason.trim() ? null : t.poRejectReasonRequired)}
            disabled={busy}
          />
          <div className="flex gap-2">
            <Button onClick={doReject} loading={busy} className="bg-destructive !text-on-accent">
              {t.poRejectConfirm}
            </Button>
            <CancelButton onClick={() => setMode("none")} disabled={busy} />
          </div>
        </div>
      )}
    </li>
  );
}

// ---- Manual queue: dispatch_failed → mark sent / retry ----

function ManualCard({
  po,
  onSuccess,
  onError,
}: {
  po: ApprovalRead;
  onSuccess: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);

  async function markSent() {
    setBusy(true);
    try {
      await reordersApi.markSent(po.id);
      onSuccess(t.poMarkedSent);
    } catch (e) {
      onError(errMessage(e));
    } finally {
      setBusy(false);
    }
  }

  // Retry = re-approve, which mints a fresh token and fires dispatch again.
  async function retry() {
    setBusy(true);
    try {
      await reordersApi.approve(po.id);
      onSuccess(t.poApproved);
    } catch (e) {
      onError(errMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-2xl border border-destructive/30 bg-destructive/5 p-4 shadow-sm">
      <POHeader po={po} />
      <POBody po={po} />

      {po.dispatch_error && (
        <p className="mt-2 text-sm text-destructive">
          {t.poDispatchError}: {po.dispatch_error}
        </p>
      )}

      <div className="mt-3 flex gap-2">
        <Button onClick={markSent} loading={busy}>
          {t.poMarkSent}
        </Button>
        <Button
          onClick={retry}
          loading={busy}
          className="bg-card !text-foreground ring-1 ring-border"
        >
          {t.poRetry}
        </Button>
      </div>
    </li>
  );
}

function CancelButton({ onClick, disabled }: { onClick: () => void; disabled: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="min-h-[44px] px-3 text-sm text-muted-foreground disabled:opacity-50"
    >
      {t.cancel}
    </button>
  );
}

// ---- List states (mirror the customers/orders pattern) ----

function Skeletons() {
  return (
    <ul className="flex flex-col gap-3" aria-hidden>
      {[0, 1].map((i) => (
        <li key={i} className="h-36 animate-pulse rounded-2xl border border-border bg-muted/50" />
      ))}
    </ul>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center text-muted-foreground">
      {t.reordersEmpty}
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
      <p className="text-destructive">{t.reordersError}</p>
      <Button onClick={onRetry} className="bg-card !text-foreground ring-1 ring-border">
        {t.retry}
      </Button>
    </div>
  );
}
