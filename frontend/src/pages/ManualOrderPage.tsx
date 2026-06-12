import { useEffect, useState } from "react";

import { inventoryApi } from "../api/inventory";
import { ordersApi } from "../api/orders";
import { ApiError } from "../api/client";
import type { InventoryRead, ManualOrderItem } from "../api/types";
import { t } from "../i18n";
import { Toast, type ToastState } from "../components/Toast";

interface FormItem {
  product_id: string;
  quantity: number;
}

export default function ManualOrderPage() {
  const [toast, setToast] = useState<ToastState | null>(null);
  const [products, setProducts] = useState<InventoryRead[]>([]);
  const [phone, setPhone] = useState("");
  const [items, setItems] = useState<FormItem[]>([{ product_id: "", quantity: 1 }]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    inventoryApi.list(100).then((page) => setProducts(page.items)).catch(() => {});
  }, []);

  function addItem() {
    setItems((prev) => [...prev, { product_id: "", quantity: 1 }]);
  }

  function removeItem(idx: number) {
    setItems((prev) => prev.filter((_, i) => i !== idx));
  }

  function updateItem(idx: number, field: keyof FormItem, value: string | number) {
    setItems((prev) =>
      prev.map((item, i) => (i === idx ? { ...item, [field]: value } : item)),
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    const validItems = items.filter((i) => i.product_id !== "");
    if (validItems.length === 0) {
      setToast({ kind: "error", message: t.manualOrderNoProducts });
      return;
    }

    setSubmitting(true);
    try {
      await ordersApi.createManual({
        customer_phone: phone.trim(),
        items: validItems as ManualOrderItem[],
      });
      setToast({ kind: "success", message: t.manualOrderSuccess });
      setPhone("");
      setItems([{ product_id: "", quantity: 1 }]);
    } catch (err: unknown) {
      const msg =
        err instanceof ApiError && err.status === 404
          ? t.manualOrderCustomerNotFound
          : err instanceof ApiError && err.status === 422
            ? t.manualOrderProductNotFound
            : t.manualOrderError;
      setToast({ kind: "error", message: msg });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h1 className="mb-6 text-xl font-bold text-foreground">{t.manualOrderTitle}</h1>

      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        {/* Customer phone */}
        <div className="flex flex-col gap-1">
          <label className="text-sm font-medium text-foreground">
            {t.manualOrderCustomerPhone}
          </label>
          <input
            type="tel"
            dir="ltr"
            required
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            className="min-h-[44px] rounded-lg border border-border bg-background px-3 text-base focus:outline-none focus:ring-2 focus:ring-primary/40"
            placeholder="+96170000000"
          />
        </div>

        {/* Order items */}
        <div className="flex flex-col gap-3">
          {items.map((item, idx) => (
            <div key={idx} className="flex items-end gap-2">
              <div className="flex-1 flex flex-col gap-1">
                <label className="text-sm font-medium text-foreground">
                  {t.manualOrderProductLabel}
                </label>
                <select
                  required
                  value={item.product_id}
                  onChange={(e) => updateItem(idx, "product_id", e.target.value)}
                  className="min-h-[44px] w-full rounded-lg border border-border bg-background px-3 text-base focus:outline-none focus:ring-2 focus:ring-primary/40"
                >
                  <option value="">—</option>
                  {products.map((p) => (
                    <option key={p.product_id} value={p.product_id}>
                      {p.name_ar}
                    </option>
                  ))}
                </select>
              </div>

              <div className="w-24 flex flex-col gap-1">
                <label className="text-sm font-medium text-foreground">
                  {t.manualOrderQtyLabel}
                </label>
                <input
                  type="number"
                  min={1}
                  required
                  value={item.quantity}
                  inputMode="numeric"
                  onChange={(e) => updateItem(idx, "quantity", Number(e.target.value))}
                  className="min-h-[44px] w-full rounded-lg border border-border bg-background px-3 text-base focus:outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>

              {items.length > 1 && (
                <button
                  type="button"
                  onClick={() => removeItem(idx)}
                  className="min-h-[44px] rounded-lg px-2 text-sm text-destructive hover:bg-destructive/10"
                >
                  {t.manualOrderRemoveItem}
                </button>
              )}
            </div>
          ))}

          <button
            type="button"
            onClick={addItem}
            className="min-h-[44px] self-start rounded-lg border border-border px-3 text-sm text-muted-foreground hover:bg-muted"
          >
            + {t.manualOrderAddItem}
          </button>
        </div>

        <button
          type="submit"
          disabled={submitting}
          className="mt-2 flex min-h-[44px] items-center justify-center rounded-lg bg-primary px-6 text-sm font-medium text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {submitting ? t.manualOrderSubmitting : t.manualOrderSubmit}
        </button>
      </form>

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </div>
  );
}
