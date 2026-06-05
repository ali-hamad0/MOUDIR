import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { billsApi } from "../api/bills";
import { ApiError } from "../api/client";
import { profileApi } from "../api/profile";
import type { BillDetail, BillLineRead, BillLineUpdate, ProductResponse } from "../api/types";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { AlertIcon } from "../components/icons";
import { Toast, type ToastState } from "../components/Toast";
import { formatDate } from "../format";
import { billStatusMeta, t } from "../i18n";

// Confidence at/below this is flagged for the owner's attention (mirrors the
// backend ocr_confidence_review_threshold; a UI signal, not an auto-commit gate).
const REVIEW_THRESHOLD = 0.75;

export default function BillReviewPage() {
  const { billId = "" } = useParams();
  const navigate = useNavigate();
  const [bill, setBill] = useState<BillDetail | null>(null);
  const [products, setProducts] = useState<ProductResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  // Per-line product mapping the owner is editing, keyed by line id.
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [detail, prods] = await Promise.all([
        billsApi.get(billId),
        profileApi.listProducts(),
      ]);
      setBill(detail);
      setProducts(prods);
      setMapping(
        Object.fromEntries(detail.lines.map((l) => [l.id, l.product_id ?? ""])),
      );
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [billId]);

  useEffect(() => {
    void load();
  }, [load]);

  const editable = bill?.status === "extracted";

  // A line carries stock if it has a quantity; such a line must be mapped before
  // approve (the backend 422s otherwise).
  const unmappedRequired = useMemo(() => {
    if (!bill) return false;
    return bill.lines.some((l) => hasQty(l) && !(mapping[l.id] ?? "").trim());
  }, [bill, mapping]);

  function errMessage(e: unknown): string {
    if (e instanceof ApiError && e.status === 0) return t.networkError;
    return t.billActionError;
  }

  async function saveLines() {
    if (!bill) return;
    setBusy(true);
    try {
      const updates: BillLineUpdate[] = bill.lines.map((l) => ({
        id: l.id,
        product_id: (mapping[l.id] ?? "").trim() || null,
      }));
      const updated = await billsApi.updateLines(bill.id, updates);
      setBill(updated);
      setToast({ kind: "success", message: t.billLinesSaved });
    } catch (e) {
      setToast({ kind: "error", message: errMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!bill) return;
    if (unmappedRequired) {
      setToast({ kind: "error", message: t.billUnmappedWarning });
      return;
    }
    setBusy(true);
    try {
      // Persist the current mapping first, then approve (the backend re-checks).
      await saveLinesSilently(bill);
      await billsApi.approve(bill.id);
      setToast({ kind: "success", message: t.billApproved });
      navigate("/bills");
    } catch (e) {
      setToast({ kind: "error", message: errMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  async function saveLinesSilently(b: BillDetail) {
    const updates: BillLineUpdate[] = b.lines.map((l) => ({
      id: l.id,
      product_id: (mapping[l.id] ?? "").trim() || null,
    }));
    await billsApi.updateLines(b.id, updates);
  }

  async function reject() {
    if (!bill) return;
    if (!reason.trim()) {
      setReasonError(t.billRejectReasonRequired);
      return;
    }
    setBusy(true);
    try {
      await billsApi.reject(bill.id, reason.trim());
      setToast({ kind: "success", message: t.billRejected });
      navigate("/bills");
    } catch (e) {
      setToast({ kind: "error", message: errMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <ReviewSkeleton />;
  if (error || !bill) return <ReviewError />;

  const s = billStatusMeta(bill.status);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <Link to="/bills" className="text-sm text-muted-foreground hover:text-foreground">
          ← {t.billBack}
        </Link>
        <span
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-current" />
          {s.label}
        </span>
      </div>

      <h2 className="text-xl font-bold text-foreground">{t.billReviewTitle}</h2>

      {/* Image beside the extracted fields. Stacks on mobile, side-by-side ≥1024px. */}
      <div className="grid gap-4 lg:grid-cols-2">
        <BillImage url={bill.image_url} />

        <div className="flex flex-col gap-4">
          <Header bill={bill} />

          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-foreground">{t.billLines}</h3>
            <ul className="flex flex-col gap-2">
              {bill.lines.map((line) => (
                <LineRow
                  key={line.id}
                  line={line}
                  products={products}
                  value={mapping[line.id] ?? ""}
                  editable={editable}
                  onChange={(pid) => setMapping((m) => ({ ...m, [line.id]: pid }))}
                />
              ))}
            </ul>
          </section>

          {editable && (
            <div className="flex flex-col gap-3 border-t border-border pt-4">
              {unmappedRequired && (
                <p className="flex items-center gap-1.5 text-sm text-status-pending">
                  <AlertIcon className="h-4 w-4" />
                  {t.billUnmappedWarning}
                </p>
              )}
              {!rejecting ? (
                <div className="flex flex-wrap gap-2">
                  <Button onClick={saveLines} loading={busy} className="bg-card !text-foreground ring-1 ring-border">
                    {t.billSaveLines}
                  </Button>
                  <Button onClick={approve} loading={busy} disabled={unmappedRequired}>
                    {t.billApprove}
                  </Button>
                  <Button
                    onClick={() => setRejecting(true)}
                    className="bg-card !text-destructive ring-1 ring-destructive/40"
                  >
                    {t.billReject}
                  </Button>
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  <Field
                    name="reject-reason"
                    label={t.billRejectReasonLabel}
                    value={reason}
                    error={reasonError}
                    onChange={(e) => {
                      setReason(e.target.value);
                      if (reasonError) setReasonError(null);
                    }}
                    onBlur={() => setReasonError(reason.trim() ? null : t.billRejectReasonRequired)}
                    disabled={busy}
                  />
                  <div className="flex gap-2">
                    <Button onClick={reject} loading={busy} className="bg-destructive !text-on-accent">
                      {t.billRejectConfirm}
                    </Button>
                    <button
                      type="button"
                      onClick={() => setRejecting(false)}
                      disabled={busy}
                      className="min-h-[44px] px-3 text-sm text-muted-foreground disabled:opacity-50"
                    >
                      {t.cancel}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}

function hasQty(line: BillLineRead): boolean {
  const q = line.quantity ? Number(line.quantity) : 0;
  return Number.isFinite(q) && q > 0;
}

function isLowConfidence(line: BillLineRead): boolean {
  if (line.confidence == null) return false;
  return Number(line.confidence) <= REVIEW_THRESHOLD;
}

function Header({ bill }: { bill: BillDetail }) {
  return (
    <dl className="flex flex-col gap-2 rounded-2xl border border-border bg-card p-4 text-sm">
      <Row label={t.billSupplier} value={bill.supplier_name || t.billNoSupplier} />
      <Row label={t.billDate} value={bill.bill_date || formatDate(bill.created_at)} />
      {bill.total_amount && (
        <Row label={t.billTotal} value={`${bill.total_amount} ${bill.currency ?? ""}`} />
      )}
    </dl>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium text-foreground">{value}</dd>
    </div>
  );
}

function LineRow({
  line,
  products,
  value,
  editable,
  onChange,
}: {
  line: BillLineRead;
  products: ProductResponse[];
  value: string;
  editable: boolean;
  onChange: (productId: string) => void;
}) {
  const low = isLowConfidence(line);
  return (
    <li className="rounded-xl border border-border bg-card p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium text-foreground">{line.name_ar || line.raw_text || "—"}</p>
          <p className="tabular text-xs text-muted-foreground">
            {t.billLineQty}: {line.quantity ?? "—"}
            {line.line_amount ? ` · ${t.billLineAmount}: ${line.line_amount}` : ""}
          </p>
        </div>
        {low && (
          <span className="flex shrink-0 items-center gap-1 rounded-full bg-status-pending/15 px-2 py-0.5 text-xs font-medium text-status-pending">
            <AlertIcon className="h-3.5 w-3.5" />
            {t.billLineLowConfidence}
          </span>
        )}
      </div>

      <label className="mt-2 flex flex-col gap-1 text-sm">
        <span className="text-muted-foreground">{t.billLineProduct}</span>
        <select
          value={value}
          disabled={!editable}
          onChange={(e) => onChange(e.target.value)}
          className="min-h-[44px] rounded-lg border border-border bg-card px-3 text-base text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/30 disabled:opacity-60"
        >
          <option value="">{t.billLinePick}</option>
          {products.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name_ar}
            </option>
          ))}
        </select>
      </label>
    </li>
  );
}

function BillImage({ url }: { url: string | null }) {
  if (!url) {
    return (
      <div className="flex aspect-[3/4] items-center justify-center rounded-2xl border border-dashed border-border bg-muted/40 text-muted-foreground">
        {t.billNoImage}
      </div>
    );
  }
  return (
    <img
      src={url}
      alt={t.billImageAlt}
      className="w-full rounded-2xl border border-border bg-card object-contain"
    />
  );
}

function ReviewSkeleton() {
  return (
    <div className="grid gap-4 lg:grid-cols-2" aria-hidden>
      <div className="aspect-[3/4] animate-pulse rounded-2xl border border-border bg-muted/50" />
      <div className="h-64 animate-pulse rounded-2xl border border-border bg-muted/50" />
    </div>
  );
}

function ReviewError() {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
      <p className="text-destructive">{t.billReviewError}</p>
      <Link to="/bills" className="text-sm text-muted-foreground hover:text-foreground">
        ← {t.billBack}
      </Link>
    </div>
  );
}
