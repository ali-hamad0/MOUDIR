import { useCallback, useState } from "react";

import { ordersApi } from "../api/orders";
import { ApiError } from "../api/client";
import type { OrderRead } from "../api/types";
import { Button } from "../components/Button";
import { CheckIcon } from "../components/icons";
import { Toast, type ToastState } from "../components/Toast";
import { formatLbp, formatTime, formatUsd } from "../format";
import { usePolling } from "../hooks/usePolling";
import { fulfillmentLabel, statusMeta, t } from "../i18n";

const POLL_MS = 5000;

export default function OrdersPage() {
  const fetcher = useCallback(() => ordersApi.today(), []);
  const { data, loading, error, refetch } = usePolling(fetcher, POLL_MS);
  const [toast, setToast] = useState<ToastState | null>(null);

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-xl font-bold text-foreground">{t.ordersToday}</h2>

      {loading ? (
        <OrderSkeletons />
      ) : error && !data ? (
        <ErrorState onRetry={refetch} />
      ) : !data || data.items.length === 0 ? (
        <EmptyState />
      ) : (
        <ul className="flex flex-col gap-3">
          {data.items.map((order) => (
            <OrderCard
              key={order.id}
              order={order}
              onCompleted={() => {
                setToast({ kind: "success", message: t.orderCompleted });
                refetch();
              }}
              onError={(message) => setToast({ kind: "error", message })}
            />
          ))}
        </ul>
      )}

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}

function OrderCard({
  order,
  onCompleted,
  onError,
}: {
  order: OrderRead;
  onCompleted: () => void;
  onError: (message: string) => void;
}) {
  const status = statusMeta(order.status);
  const usd = formatUsd(order.total_usd);
  // The complete action is offered only on a confirmed order — completion
  // deducts stock and is irreversible, so it sits behind an inline confirm.
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const canComplete = order.status === "confirmed";

  async function complete() {
    setBusy(true);
    try {
      await ordersApi.complete(order.id);
      setConfirming(false);
      onCompleted();
    } catch (e) {
      if (e instanceof ApiError && e.status === 0) onError(t.networkError);
      else if (e instanceof ApiError && e.status === 409) onError(t.orderNotCompletable);
      else onError(t.orderCompleteError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold text-foreground">
            {order.customer_display_name || t.unknownCustomer}
          </p>
          <p className="text-xs text-muted-foreground">
            {fulfillmentLabel(order.fulfillment_type)}
            {order.requested_time_text ? ` · ${order.requested_time_text}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {/* Status: dot + label + color — never color alone. */}
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${status.cls}`}
          >
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
            {status.label}
          </span>
          <span className="tabular text-xs text-muted-foreground">
            {formatTime(order.created_at)}
          </span>
        </div>
      </div>

      <ul className="mt-3 flex flex-col gap-1 border-t border-border pt-3 text-sm">
        {order.items.map((item) => (
          <li key={item.id} className="flex justify-between gap-2">
            <span className="truncate text-foreground">
              <span className="tabular text-muted-foreground">
                {item.quantity} {t.qtyTimes}
              </span>{" "}
              {item.name_ar_snapshot}
            </span>
            <span className="tabular shrink-0 text-muted-foreground">
              {formatLbp(item.line_total_lbp)}
            </span>
          </li>
        ))}
      </ul>

      {/* Total: LBP primary, USD muted secondary (both from the snapshot). */}
      <div className="mt-3 flex items-baseline justify-end gap-2">
        <span className="tabular text-lg font-bold text-foreground">
          {formatLbp(order.total_lbp)}
        </span>
        {usd && (
          <span className="tabular text-sm text-muted-foreground">{usd}</span>
        )}
      </div>

      {canComplete && !confirming && (
        <div className="mt-3 flex justify-end border-t border-border pt-3">
          <Button onClick={() => setConfirming(true)} className="bg-accent">
            <CheckIcon className="h-4 w-4" />
            {t.markComplete}
          </Button>
        </div>
      )}

      {canComplete && confirming && (
        <div className="mt-3 flex flex-col gap-3 border-t border-border pt-3">
          <div>
            <p className="font-medium text-foreground">{t.markCompleteConfirmTitle}</p>
            <p className="mt-1 text-sm text-muted-foreground">{t.markCompleteConfirmBody}</p>
          </div>
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setConfirming(false)}
              disabled={busy}
              className="min-h-[44px] rounded-lg px-4 text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              {t.cancel}
            </button>
            <Button onClick={complete} loading={busy} className="bg-accent">
              {t.markCompleteConfirm}
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}

function OrderSkeletons() {
  return (
    <ul className="flex flex-col gap-3" aria-hidden>
      {[0, 1, 2].map((i) => (
        <li
          key={i}
          className="h-28 animate-pulse rounded-2xl border border-border bg-muted/50"
        />
      ))}
    </ul>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center">
      <p className="font-medium text-foreground">{t.noOrdersYet}</p>
      <p className="mt-1 text-sm text-muted-foreground">{t.noOrdersHint}</p>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
      <p className="text-destructive">{t.ordersError}</p>
      <Button
        onClick={onRetry}
        className="bg-card !text-foreground ring-1 ring-border"
      >
        {t.retry}
      </Button>
    </div>
  );
}
