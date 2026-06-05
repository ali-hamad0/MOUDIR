import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { inventoryApi } from "../api/inventory";
import { profileApi } from "../api/profile";
import type {
  DayHours,
  PolicyUpsert,
  ProductResponse,
  ProductWrite,
  ProfileUpsert,
} from "../api/types";
import { useAuth } from "../auth/context";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Toggle } from "../components/Toggle";
import { DAY_LABELS, t } from "../i18n";

const STEP_TITLES = [
  t.stepBusinessTitle,
  t.stepProductsTitle,
  t.stepHoursTitle,
  t.stepPoliciesTitle,
];

export default function SetupWizard() {
  const navigate = useNavigate();
  const { refreshMe } = useAuth();

  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // ---- Step state ----
  const [profile, setProfile] = useState<ProfileUpsert>({
    business_name: "",
    description: "",
    location: "",
    delivery_radius_km: null,
    accepts_delivery: false,
    accepts_pickup: true,
  });
  // Server-backed: products are created immediately when added (and loaded on
  // entering the step), so the list reflects what's actually in the store and
  // survives navigating back/forward.
  const [products, setProducts] = useState<ProductResponse[]>([]);
  const [hours, setHours] = useState<DayHours[]>(
    DAY_LABELS.map((_, i) => ({
      day_of_week: i,
      open_time: "08:00",
      close_time: "20:00",
      is_closed: false,
      note_ar: null,
    })),
  );
  const [policies, setPolicies] = useState<Record<string, string>>({
    min_order_lbp: "",
    delivery_fee_lbp: "",
    delivery_zones: "",
    payment_methods: "",
  });

  // Each step persists its own slice to its existing endpoint, then advances —
  // so progress is saved as you go (form-autosave) and a later failure never
  // loses earlier steps.
  async function persistAndAdvance() {
    setError(null);
    setBusy(true);
    try {
      if (step === 0) {
        if (!profile.business_name?.trim()) {
          setError(t.required);
          return;
        }
        await profileApi.saveProfile(profile);
        setStep(1);
      } else if (step === 1) {
        // Products are created the moment they're added (in ProductsStep), so by
        // here they already exist server-side; we only guard that there's at least
        // one and advance. (No batch create → no duplicate-on-reentry.)
        if (products.length === 0) {
          setError(t.atLeastOneProduct);
          return;
        }
        setStep(2);
      } else if (step === 2) {
        await profileApi.replaceHours(hours);
        setStep(3);
      } else {
        const entries: PolicyUpsert[] = Object.entries(policies)
          .filter(([, v]) => v.trim() !== "")
          .map(([key, value]) => ({ key, value }));
        await profileApi.savePolicies(entries);
        await refreshMe(); // setup_complete flips → banner clears (Task 3.10)
        navigate("/", { replace: true });
      }
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 0
          ? t.networkError
          : t.errorGeneric,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h2 className="text-xl font-bold text-foreground">{t.wizardWelcome}</h2>
        <StepIndicator current={step} count={STEP_TITLES.length} />
        <p className="mt-2 text-sm font-medium text-primary">
          {STEP_TITLES[step]}
        </p>
      </header>

      <section className="rounded-2xl border border-border bg-card p-5 shadow-sm">
        {step === 0 && <BusinessStep value={profile} onChange={setProfile} />}
        {step === 1 && (
          <ProductsStep products={products} onChange={setProducts} />
        )}
        {step === 2 && <HoursStep hours={hours} onChange={setHours} />}
        {step === 3 && (
          <PoliciesStep policies={policies} onChange={setPolicies} />
        )}
      </section>

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setStep((s) => Math.max(0, s - 1))}
          disabled={step === 0 || busy}
          className="min-h-[44px] rounded-lg px-4 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
        >
          {t.back}
        </button>
        <Button onClick={persistAndAdvance} loading={busy}>
          {step === STEP_TITLES.length - 1 ? t.finish : t.next}
        </Button>
      </div>
    </div>
  );
}

function StepIndicator({ current, count }: { current: number; count: number }) {
  return (
    <div
      className="mt-3 flex items-center gap-1.5"
      aria-label={`${t.wizardStep} ${current + 1} ${t.wizardOf} ${count}`}
    >
      {Array.from({ length: count }).map((_, i) => (
        <span
          key={i}
          className={`h-1.5 flex-1 rounded-full transition-colors ${
            i <= current ? "bg-primary" : "bg-border"
          }`}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- Step 1
function BusinessStep({
  value,
  onChange,
}: {
  value: ProfileUpsert;
  onChange: (v: ProfileUpsert) => void;
}) {
  const set = (patch: Partial<ProfileUpsert>) =>
    onChange({ ...value, ...patch });
  return (
    <div className="flex flex-col gap-4">
      <Field
        name="business_name"
        label={t.businessName}
        value={value.business_name ?? ""}
        onChange={(e) => set({ business_name: e.target.value })}
      />
      <Field
        name="location"
        label={`${t.businessLocation} (${t.optional})`}
        value={value.location ?? ""}
        onChange={(e) => set({ location: e.target.value })}
      />
      <Field
        name="delivery_radius_km"
        label={`${t.deliveryRadiusKm} (${t.optional})`}
        type="number"
        inputMode="numeric"
        min={0}
        value={value.delivery_radius_km ?? ""}
        onChange={(e) =>
          set({
            delivery_radius_km: e.target.value ? Number(e.target.value) : null,
          })
        }
      />
      <Toggle
        label={t.acceptsDelivery}
        checked={value.accepts_delivery}
        onChange={(v) => set({ accepts_delivery: v })}
      />
      <Toggle
        label={t.acceptsPickup}
        checked={value.accepts_pickup}
        onChange={(v) => set({ accepts_pickup: v })}
      />
    </div>
  );
}

// ---------------------------------------------------------------- Step 2
function ProductsStep({
  products,
  onChange,
}: {
  products: ProductResponse[];
  onChange: (p: ProductResponse[]) => void;
}) {
  const blankDraft = {
    name_ar: "",
    price_lbp: "" as string,
    price_usd: "" as string,
    is_available: true,
    quantity: "" as string,
  };
  const [draft, setDraft] = useState(blankDraft);
  const [adding, setAdding] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [addError, setAddError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  // Load the store's existing products once, so the page SHOWS what's already
  // there (the previous wizard only kept newly-typed products in local state and
  // never fetched, so the catalog always looked empty on return).
  useEffect(() => {
    let alive = true;
    profileApi
      .listProducts()
      .then((rows) => {
        if (alive) onChange(rows);
      })
      .catch(() => {
        if (alive) setLoadError(t.productLoadError);
      })
      .finally(() => {
        if (alive) setLoaded(true);
      });
    return () => {
      alive = false; // ignore a late response after the step unmounts
    };
    // onChange identity is stable from the parent for this step's lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function addProduct() {
    if (!draft.name_ar.trim()) return;
    setAdding(true);
    setAddError(null);
    try {
      const created = await profileApi.createProduct({
        name_ar: draft.name_ar.trim(),
        price_lbp: draft.price_lbp ? Number(draft.price_lbp) : null,
        price_usd: draft.price_usd || null,
        is_available: draft.is_available,
      } as ProductWrite);
      // Optionally seed the inventory row so stock shows up immediately.
      if (draft.quantity.trim() && Number(draft.quantity) > 0) {
        await inventoryApi.upsert(created.id, {
          quantity: Number(draft.quantity),
          reorder_threshold: null,
          reorder_quantity: null,
          supplier_id: null,
        });
      }
      onChange([...products, created]);
      setDraft(blankDraft);
    } catch (err) {
      setAddError(
        err instanceof ApiError && err.status === 0 ? t.networkError : t.errorGeneric,
      );
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <h3 className="text-sm font-semibold text-foreground">{t.productsInStore}</h3>

      {!loaded ? (
        <p className="text-sm text-muted-foreground">{t.loading}</p>
      ) : loadError ? (
        <p className="text-sm text-destructive">{loadError}</p>
      ) : products.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t.noProductsYet}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {products.map((p) => (
            <li
              key={p.id}
              className="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-sm"
            >
              <span className="font-medium text-foreground">{p.name_ar}</span>
              <span className="tabular text-muted-foreground">
                {p.price_lbp ? `${p.price_lbp} ل.ل.` : "—"}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-col gap-3 rounded-lg border border-dashed border-border p-3">
        <Field
          name="name_ar"
          label={t.productNameAr}
          value={draft.name_ar}
          onChange={(e) => setDraft({ ...draft, name_ar: e.target.value })}
        />
        <div className="grid grid-cols-2 gap-3">
          <Field
            name="price_lbp"
            label={t.priceLbp}
            type="number"
            inputMode="numeric"
            min={0}
            value={draft.price_lbp}
            onChange={(e) => setDraft({ ...draft, price_lbp: e.target.value })}
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
        />
        <Toggle
          label={t.isAvailable}
          checked={draft.is_available}
          onChange={(v) => setDraft({ ...draft, is_available: v })}
        />
        {addError && <p className="text-sm text-destructive">{addError}</p>}
        <Button
          type="button"
          onClick={addProduct}
          loading={adding}
          disabled={!draft.name_ar.trim()}
          className="bg-card !text-primary ring-1 ring-primary/40"
        >
          {adding ? t.addingProduct : t.addProduct}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Step 3
function HoursStep({
  hours,
  onChange,
}: {
  hours: DayHours[];
  onChange: (h: DayHours[]) => void;
}) {
  const setDay = (i: number, patch: Partial<DayHours>) =>
    onChange(hours.map((d, j) => (j === i ? { ...d, ...patch } : d)));

  return (
    <div className="flex flex-col gap-3">
      {hours.map((d, i) => (
        <div key={d.day_of_week} className="rounded-lg border border-border p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">{DAY_LABELS[i]}</span>
            <Toggle
              label={t.closed}
              checked={d.is_closed}
              onChange={(v) => setDay(i, { is_closed: v })}
            />
          </div>
          {!d.is_closed && (
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Field
                name={`open-${i}`}
                label={t.openTime}
                type="time"
                dir="ltr"
                value={d.open_time ?? ""}
                onChange={(e) => setDay(i, { open_time: e.target.value })}
              />
              <Field
                name={`close-${i}`}
                label={t.closeTime}
                type="time"
                dir="ltr"
                value={d.close_time ?? ""}
                onChange={(e) => setDay(i, { close_time: e.target.value })}
              />
            </div>
          )}
          <div className="mt-3">
            <Field
              name={`note-${i}`}
              label={`${t.ramadanNote} (${t.optional})`}
              value={d.note_ar ?? ""}
              onChange={(e) => setDay(i, { note_ar: e.target.value || null })}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- Step 4
function PoliciesStep({
  policies,
  onChange,
}: {
  policies: Record<string, string>;
  onChange: (p: Record<string, string>) => void;
}) {
  const set = (key: string, value: string) =>
    onChange({ ...policies, [key]: value });
  return (
    <div className="flex flex-col gap-4">
      <Field
        name="min_order_lbp"
        label={`${t.minOrderLbp} (${t.optional})`}
        type="number"
        inputMode="numeric"
        min={0}
        value={policies.min_order_lbp}
        onChange={(e) => set("min_order_lbp", e.target.value)}
      />
      <Field
        name="delivery_fee_lbp"
        label={`${t.deliveryFeeLbp} (${t.optional})`}
        type="number"
        inputMode="numeric"
        min={0}
        value={policies.delivery_fee_lbp}
        onChange={(e) => set("delivery_fee_lbp", e.target.value)}
      />
      <Field
        name="delivery_zones"
        label={`${t.deliveryZones} (${t.optional})`}
        value={policies.delivery_zones}
        onChange={(e) => set("delivery_zones", e.target.value)}
      />
      <Field
        name="payment_methods"
        label={`${t.paymentMethods} (${t.optional})`}
        value={policies.payment_methods}
        onChange={(e) => set("payment_methods", e.target.value)}
      />
    </div>
  );
}
