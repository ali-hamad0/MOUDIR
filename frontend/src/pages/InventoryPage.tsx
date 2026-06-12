import { useEffect, useMemo, useState } from "react";

import { ApiError } from "../api/client";
import { inventoryApi } from "../api/inventory";
import { profileApi } from "../api/profile";
import type {
  InventoryPage as Page,
  InventoryRead,
  ProductWrite,
  SupplierRead,
} from "../api/types";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { ProLimitNotice } from "../components/ProLock";
import { Toggle } from "../components/Toggle";
import { AlertIcon } from "../components/icons";
import { t } from "../i18n";

const PAGE_SIZE = 20;

export default function InventoryPage() {
  const [page, setPage] = useState<Page | null>(null);
  const [suppliers, setSuppliers] = useState<SupplierRead[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  // The product currently being edited (null = no modal open).
  const [editing, setEditing] = useState<InventoryRead | null>(null);
  const [addingProduct, setAddingProduct] = useState(false);

  // Suppliers are a small per-tenant list; load once for name lookup + the edit
  // dropdown. A failure here is non-fatal — the page still renders without it.
  useEffect(() => {
    let cancelled = false;
    inventoryApi
      .suppliers()
      .then((rows) => !cancelled && setSuppliers(rows))
      .catch(() => {
        /* non-fatal: inventory still lists, supplier shows as id-less */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    inventoryApi
      .list(PAGE_SIZE, offset)
      .then((data) => !cancelled && setPage(data))
      .catch((err) => {
        if (!cancelled) setError(!(err instanceof ApiError && err.status === 401));
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [offset, reloadTick]);

  const supplierName = useMemo(() => {
    const byId = new Map(suppliers.map((s) => [s.id, s.name]));
    return (id: string | null) => (id ? (byId.get(id) ?? t.notSet) : t.noSupplier);
  }, [suppliers]);

  const rows = page?.items ?? [];
  const total = page?.total ?? 0;
  const pageNum = Math.floor(offset / PAGE_SIZE) + 1;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function reload() {
    setReloadTick((n) => n + 1);
  }

  function onSaved(updated: InventoryRead) {
    // Reflect the saved row in place so the badge/values update without a refetch.
    setPage((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((r) =>
              r.product_id === updated.product_id ? updated : r,
            ),
          }
        : prev,
    );
    setEditing(null);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xl font-bold text-foreground">{t.inventoryTitle}</h2>
        <Button type="button" onClick={() => setAddingProduct(true)}>
          {t.addProduct}
        </Button>
      </div>

      {loading ? (
        <Skeletons />
      ) : error && !page ? (
        <ErrorState onRetry={reload} />
      ) : rows.length === 0 ? (
        <EmptyState />
      ) : (
        <>
          {/* Mobile (360px+): cards. Desktop (lg ≥1024px): table. */}
          <ul className="flex flex-col gap-3 lg:hidden">
            {rows.map((r) => (
              <InventoryCard
                key={r.product_id}
                row={r}
                supplierName={supplierName(r.supplier_id)}
                onEdit={() => setEditing(r)}
              />
            ))}
          </ul>

          <div className="hidden overflow-x-auto lg:block">
            <table className="w-full text-start text-sm">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="py-2 text-start font-medium">{t.colProduct}</th>
                  <th className="py-2 text-start font-medium">{t.colQuantity}</th>
                  <th className="py-2 text-start font-medium">{t.colThreshold}</th>
                  <th className="py-2 text-start font-medium">{t.colReorderQty}</th>
                  <th className="py-2 text-start font-medium">{t.colSupplier}</th>
                  <th className="py-2 text-end font-medium" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.product_id} className="border-b border-border last:border-0">
                    <td className="py-3">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-foreground">{r.name_ar}</span>
                        {r.is_low && <LowStockBadge />}
                      </div>
                    </td>
                    <td className="tabular py-3">{r.quantity}</td>
                    <td className="tabular py-3">{numOrDash(r.reorder_threshold)}</td>
                    <td className="tabular py-3">{numOrDash(r.reorder_quantity)}</td>
                    <td className="py-3 text-muted-foreground">{supplierName(r.supplier_id)}</td>
                    <td className="py-3 text-end">
                      <button
                        type="button"
                        onClick={() => setEditing(r)}
                        className="min-h-[44px] rounded-lg px-3 text-sm font-medium text-primary hover:bg-primary/10"
                      >
                        {t.edit}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Pager
            pageNum={pageNum}
            pageCount={pageCount}
            onPrev={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            onNext={() => setOffset((o) => o + PAGE_SIZE)}
          />
        </>
      )}

      {editing && (
        <EditModal
          row={editing}
          suppliers={suppliers}
          onClose={() => setEditing(null)}
          onSaved={onSaved}
        />
      )}

      {addingProduct && (
        <AddProductModal
          onClose={() => setAddingProduct(false)}
          onCreated={() => {
            setAddingProduct(false);
            reload();
          }}
        />
      )}
    </div>
  );
}

function numOrDash(n: number | null): string {
  return n == null ? "—" : String(n);
}

// Low-stock indicator: icon + color + Arabic label together (never color alone).
function LowStockBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-2 py-0.5 text-xs font-medium text-destructive">
      <AlertIcon className="h-3.5 w-3.5" />
      {t.lowStockBadge}
    </span>
  );
}

function InventoryCard({
  row,
  supplierName,
  onEdit,
}: {
  row: InventoryRead;
  supplierName: string;
  onEdit: () => void;
}) {
  return (
    <li className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold text-foreground">{row.name_ar}</p>
          <p className="text-xs text-muted-foreground">{supplierName}</p>
        </div>
        {row.is_low && <LowStockBadge />}
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 border-t border-border pt-3 text-xs text-muted-foreground">
        <Stat label={t.colQuantity} value={String(row.quantity)} />
        <Stat label={t.colThreshold} value={numOrDash(row.reorder_threshold)} />
        <Stat label={t.colReorderQty} value={numOrDash(row.reorder_quantity)} />
      </dl>
      <div className="mt-3 flex justify-end">
        <button
          type="button"
          onClick={onEdit}
          className="min-h-[44px] rounded-lg px-3 text-sm font-medium text-primary hover:bg-primary/10"
        >
          {t.edit}
        </button>
      </div>
    </li>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt>{label}</dt>
      <dd className="tabular text-sm font-medium text-foreground">{value}</dd>
    </div>
  );
}

// ---- Edit modal ----

// "" maps to a cleared (null) optional field; the validator allows empty for the
// nullable fields but requires quantity ≥ 0.
interface FormState {
  quantity: string;
  reorder_threshold: string;
  reorder_quantity: string;
  supplier_id: string;
}

function EditModal({
  row,
  suppliers,
  onClose,
  onSaved,
}: {
  row: InventoryRead;
  suppliers: SupplierRead[];
  onClose: () => void;
  onSaved: (updated: InventoryRead) => void;
}) {
  const [form, setForm] = useState<FormState>({
    quantity: String(row.quantity),
    reorder_threshold: row.reorder_threshold == null ? "" : String(row.reorder_threshold),
    reorder_quantity: row.reorder_quantity == null ? "" : String(row.reorder_quantity),
    supplier_id: row.supplier_id ?? "",
  });
  // Per-field errors, surfaced on blur (the Phase 3 form convention).
  const [errors, setErrors] = useState<Partial<Record<keyof FormState, string>>>({});
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  function set<K extends keyof FormState>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  // A required non-negative integer (quantity), or an optional one (thresholds).
  function validateNum(value: string, required: boolean): string | null {
    if (value.trim() === "") return required ? t.required : null;
    const n = Number(value);
    if (!Number.isInteger(n) || n < 0) return t.mustBeZeroOrMore;
    return null;
  }

  function blurValidate(key: keyof FormState) {
    if (key === "supplier_id") return;
    const err = validateNum(form[key], key === "quantity");
    setErrors((e) => ({ ...e, [key]: err ?? undefined }));
  }

  function parseOptional(value: string): number | null {
    return value.trim() === "" ? null : Number(value);
  }

  async function save() {
    const nextErrors = {
      quantity: validateNum(form.quantity, true) ?? undefined,
      reorder_threshold: validateNum(form.reorder_threshold, false) ?? undefined,
      reorder_quantity: validateNum(form.reorder_quantity, false) ?? undefined,
    };
    setErrors(nextErrors);
    if (Object.values(nextErrors).some(Boolean)) return;

    setBusy(true);
    setFormError(null);
    try {
      const updated = await inventoryApi.upsert(row.product_id, {
        quantity: Number(form.quantity),
        reorder_threshold: parseOptional(form.reorder_threshold),
        reorder_quantity: parseOptional(form.reorder_quantity),
        supplier_id: form.supplier_id === "" ? null : form.supplier_id,
      });
      onSaved(updated);
    } catch (e) {
      setFormError(
        e instanceof ApiError && e.status === 0 ? t.networkError : t.inventorySaveError,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center bg-foreground/40 p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={t.editInventoryTitle.replace("{name}", row.name_ar)}
      onClick={onClose}
    >
      <div
        className="flex max-h-[90dvh] w-full max-w-md flex-col gap-4 overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-bold text-foreground">
          {t.editInventoryTitle.replace("{name}", row.name_ar)}
        </h3>

        <Field
          name="quantity"
          label={t.fieldQuantity}
          type="number"
          inputMode="numeric"
          min={0}
          dir="ltr"
          value={form.quantity}
          error={errors.quantity}
          onChange={(e) => set("quantity", e.target.value)}
          onBlur={() => blurValidate("quantity")}
          disabled={busy}
        />
        <div className="flex flex-col gap-1">
          <Field
            name="reorder_threshold"
            label={t.fieldThreshold}
            type="number"
            inputMode="numeric"
            min={0}
            dir="ltr"
            value={form.reorder_threshold}
            error={errors.reorder_threshold}
            onChange={(e) => set("reorder_threshold", e.target.value)}
            onBlur={() => blurValidate("reorder_threshold")}
            disabled={busy}
          />
          <p className="text-xs text-muted-foreground">{t.fieldThresholdHint}</p>
        </div>
        <Field
          name="reorder_quantity"
          label={t.fieldReorderQty}
          type="number"
          inputMode="numeric"
          min={0}
          dir="ltr"
          value={form.reorder_quantity}
          error={errors.reorder_quantity}
          onChange={(e) => set("reorder_quantity", e.target.value)}
          onBlur={() => blurValidate("reorder_quantity")}
          disabled={busy}
        />

        <div className="flex flex-col gap-1.5 text-start">
          <label htmlFor="supplier_id" className="text-sm font-medium text-foreground">
            {t.fieldSupplier}
          </label>
          <select
            id="supplier_id"
            name="supplier_id"
            value={form.supplier_id}
            onChange={(e) => set("supplier_id", e.target.value)}
            disabled={busy}
            className="min-h-[44px] rounded-lg border border-border bg-card px-3 text-base text-foreground outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/30"
          >
            <option value="">{t.noSupplier}</option>
            {suppliers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>

        {formError && (
          <p role="alert" className="text-sm text-destructive">
            {formError}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="min-h-[44px] rounded-lg px-4 text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            {t.cancel}
          </button>
          <Button onClick={save} loading={busy}>
            {busy ? t.saving : t.save}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---- Add product modal ----

// Creating a product here (not in the profile page) keeps catalog + stock in
// one place. The backend seeds an inventory row at qty 0 on create, so the new
// product shows up in the list immediately; an optional initial quantity is
// applied right after with the regular upsert.
function AddProductModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [draft, setDraft] = useState({
    name_ar: "",
    name_en: "",
    price_lbp: "",
    price_usd: "",
    unit: "",
    category: "",
    is_available: true,
    quantity: "",
  });
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function create() {
    if (!draft.name_ar.trim()) return;
    setBusy(true);
    setFormError(null);
    try {
      const created = await profileApi.createProduct({
        name_ar: draft.name_ar.trim(),
        name_en: draft.name_en.trim() || null,
        unit: draft.unit.trim() || null,
        category: draft.category.trim() || null,
        price_lbp: draft.price_lbp ? Number(draft.price_lbp) : null,
        price_usd: draft.price_usd || null,
        is_available: draft.is_available,
      } as ProductWrite);

      // Non-fatal: the product exists either way; stock can be set from the list.
      if (draft.quantity.trim() && Number(draft.quantity) > 0) {
        await inventoryApi
          .upsert(created.id, {
            quantity: Number(draft.quantity),
            reorder_threshold: null,
            reorder_quantity: null,
            supplier_id: null,
          })
          .catch(() => {});
      }
      onCreated();
    } catch (err) {
      // 402 = the Free catalog cap (plan_gate) — show the upgrade nudge.
      setFormError(
        err instanceof ApiError && err.status === 402
          ? "limit:products"
          : err instanceof ApiError && err.status === 0
            ? t.networkError
            : t.errorGeneric,
      );
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-end justify-center bg-foreground/40 p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={t.addProduct}
      onClick={onClose}
    >
      <div
        className="flex max-h-[90dvh] w-full max-w-md flex-col gap-4 overflow-y-auto rounded-2xl border border-border bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-bold text-foreground">{t.addProduct}</h3>

        <Field
          name="name_ar"
          label={t.productNameAr}
          value={draft.name_ar}
          onChange={(e) => setDraft({ ...draft, name_ar: e.target.value })}
          disabled={busy}
        />
        <Field
          name="name_en"
          label={`${t.productNameEn} (${t.optional})`}
          value={draft.name_en}
          onChange={(e) => setDraft({ ...draft, name_en: e.target.value })}
          disabled={busy}
        />
        <div className="grid grid-cols-2 gap-3">
          <Field
            name="unit"
            label={`${t.unit} (${t.optional})`}
            placeholder="حبة، كيلو، ربطة..."
            value={draft.unit}
            onChange={(e) => setDraft({ ...draft, unit: e.target.value })}
            disabled={busy}
          />
          <Field
            name="category"
            label={`${t.category} (${t.optional})`}
            placeholder="مخبوزات، حلويات..."
            value={draft.category}
            onChange={(e) => setDraft({ ...draft, category: e.target.value })}
            disabled={busy}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field
            name="price_lbp"
            label={t.priceLbp}
            type="number"
            inputMode="numeric"
            min={0}
            value={draft.price_lbp}
            onChange={(e) => setDraft({ ...draft, price_lbp: e.target.value })}
            disabled={busy}
          />
          <Field
            name="price_usd"
            label={t.priceUsd}
            type="number"
            inputMode="decimal"
            min={0}
            step="0.01"
            value={draft.price_usd}
            onChange={(e) => setDraft({ ...draft, price_usd: e.target.value })}
            disabled={busy}
          />
        </div>
        <Field
          name="quantity"
          label={t.initialStock}
          type="number"
          inputMode="numeric"
          min={0}
          value={draft.quantity}
          onChange={(e) => setDraft({ ...draft, quantity: e.target.value })}
          disabled={busy}
        />
        <Toggle
          label={t.isAvailable}
          checked={draft.is_available}
          onChange={(v) => setDraft({ ...draft, is_available: v })}
        />

        {formError === "limit:products" ? (
          <ProLimitNotice message={t.limitProductsMsg} />
        ) : formError ? (
          <p role="alert" className="text-sm text-destructive">
            {formError}
          </p>
        ) : null}

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="min-h-[44px] rounded-lg px-4 text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-50"
          >
            {t.cancel}
          </button>
          <Button onClick={create} loading={busy} disabled={!draft.name_ar.trim()}>
            {busy ? t.addingProduct : t.addProduct}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ---- Shared list states (mirror the customers/orders pattern) ----

function Pager({
  pageNum,
  pageCount,
  onPrev,
  onNext,
}: {
  pageNum: number;
  pageCount: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (pageCount <= 1) return null;
  return (
    <div className="flex items-center justify-between gap-3">
      <button
        type="button"
        onClick={onPrev}
        disabled={pageNum <= 1}
        className="min-h-[44px] rounded-lg px-4 text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-40"
      >
        {t.prevPage}
      </button>
      <span className="tabular text-sm text-muted-foreground">
        {t.pageOf.replace("{n}", String(pageNum)).replace("{total}", String(pageCount))}
      </span>
      <button
        type="button"
        onClick={onNext}
        disabled={pageNum >= pageCount}
        className="min-h-[44px] rounded-lg px-4 text-sm font-medium text-muted-foreground hover:text-foreground disabled:opacity-40"
      >
        {t.nextPage}
      </button>
    </div>
  );
}

function Skeletons() {
  return (
    <ul className="flex flex-col gap-3" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <li key={i} className="h-20 animate-pulse rounded-2xl border border-border bg-muted/50" />
      ))}
    </ul>
  );
}

function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-border bg-card p-8 text-center">
      <p className="font-medium text-foreground">{t.noInventoryYet}</p>
      <p className="mt-1 text-sm text-muted-foreground">{t.noInventoryHint}</p>
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 p-8 text-center">
      <p className="text-destructive">{t.inventoryError}</p>
      <Button onClick={onRetry} className="bg-card !text-foreground ring-1 ring-border">
        {t.retry}
      </Button>
    </div>
  );
}
