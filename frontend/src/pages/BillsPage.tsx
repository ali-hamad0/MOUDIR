import { useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { billsApi } from "../api/bills";
import { ApiError } from "../api/client";
import type { BillRead } from "../api/types";
import { Button } from "../components/Button";
import { Toast, type ToastState } from "../components/Toast";
import { formatDate } from "../format";
import { usePolling } from "../hooks/usePolling";
import { billStatusMeta, t } from "../i18n";

// Poll so an uploaded bill moves uploaded → processing → extracted (the worker
// lands it within a few seconds) without the owner refreshing.
const POLL_MS = 5000;

const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif";

export default function BillsPage() {
  const fetcher = useCallback(() => billsApi.list(), []);
  const { data, loading, error, refetch } = usePolling(fetcher, POLL_MS);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  async function onFile(file: File) {
    if (!file.type.startsWith("image/")) {
      setToast({ kind: "error", message: t.billUploadBadType });
      return;
    }
    setUploading(true);
    try {
      await billsApi.upload(file);
      setToast({ kind: "success", message: t.billUploaded });
      refetch();
    } catch (e) {
      // 402 = the Free monthly bill quota (plan_gate) — nudge toward Pro.
      const msg =
        e instanceof ApiError && e.status === 402
          ? t.limitBillsMsg
          : e instanceof ApiError && e.status === 0
            ? t.networkError
            : t.billUploadError;
      setToast({ kind: "error", message: msg });
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = ""; // allow re-uploading the same file
    }
  }

  const items = data?.items ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xl font-bold text-foreground">{t.billsTitle}</h2>
        <Button onClick={() => inputRef.current?.click()} loading={uploading}>
          {uploading ? t.billUploading : t.billUpload}
        </Button>
        {/* capture lets a phone open the camera directly; falls back to the gallery. */}
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          capture="environment"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void onFile(file);
          }}
        />
      </div>

      {loading ? (
        <Skeletons />
      ) : error && !data ? (
        <ErrorState onRetry={refetch} />
      ) : items.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((bill) => (
            <BillCard key={bill.id} bill={bill} />
          ))}
        </ul>
      )}

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const s = billStatusMeta(status);
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {s.label}
    </span>
  );
}

// Bills with something to look at are clickable through to the review screen;
// a bill still being OCR'd (uploaded/processing) has nothing to show yet.
const REVIEWABLE = new Set(["extracted", "ocr_failed", "committed", "rejected"]);

function BillCard({ bill }: { bill: BillRead }) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        {/* Thumbnail of the scan itself — the owner sees WHICH receipt this is
            without opening the review screen. */}
        {bill.image_url && (
          <img
            src={bill.image_url}
            alt={t.billImageAlt}
            loading="lazy"
            className="h-16 w-16 shrink-0 rounded-lg border border-border bg-muted object-cover"
          />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold text-foreground">
            {bill.supplier_name || t.billNoSupplier}
          </p>
          <p className="tabular text-xs text-muted-foreground">
            {bill.bill_date || formatDate(bill.created_at)}
          </p>
        </div>
        <StatusBadge status={bill.status} />
      </div>

      {(bill.total_amount || bill.reject_reason) && (
        <dl className="mt-3 flex flex-col gap-2 border-t border-border pt-3 text-sm">
          {bill.total_amount && (
            <div className="flex justify-between gap-2">
              <dt className="text-muted-foreground">{t.billTotal}</dt>
              <dd className="tabular font-medium text-foreground">
                {bill.total_amount} {bill.currency ?? ""}
              </dd>
            </div>
          )}
          {bill.reject_reason && (
            <div className="flex flex-col gap-0.5">
              <dt className="text-muted-foreground">{t.poDispatchError}</dt>
              <dd className="text-destructive">{bill.reject_reason}</dd>
            </div>
          )}
        </dl>
      )}
    </>
  );

  if (REVIEWABLE.has(bill.status)) {
    return (
      <li>
        <Link
          to={`/bills/${bill.id}`}
          className="block rounded-2xl border border-border bg-card p-4 shadow-sm transition-colors hover:border-primary/40"
        >
          {body}
        </Link>
      </li>
    );
  }
  return <li className="rounded-2xl border border-border bg-card p-4 shadow-sm">{body}</li>;
}

function Skeletons() {
  return (
    <ul className="flex flex-col gap-3" aria-hidden>
      {[0, 1].map((i) => (
        <li key={i} className="h-24 animate-pulse rounded-2xl border border-border bg-muted/50" />
      ))}
    </ul>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center text-muted-foreground">
      {t.billsEmpty}
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
      <p className="text-destructive">{t.billsError}</p>
      <Button onClick={onRetry} className="bg-card !text-foreground ring-1 ring-border">
        {t.retry}
      </Button>
    </div>
  );
}
