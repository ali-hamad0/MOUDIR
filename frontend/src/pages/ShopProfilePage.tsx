import { type ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { profileApi } from "../api/profile";
import type { DayHours, PolicyUpsert, ProfileUpsert } from "../api/types";
import { useAuth } from "../auth/context";
import { Button } from "../components/Button";
import { Field } from "../components/Field";
import { Toggle } from "../components/Toggle";
import { DAY_LABELS, t } from "../i18n";

// The shop profile page: every business detail in one place, always editable.
// Each section is its own card with its own save — filling one section never
// risks losing another, and a returning owner sees everything pre-filled.
// Products are intentionally NOT here: they're managed from the Inventory page.
export default function ShopProfilePage() {
  const { me, refreshMe } = useAuth();

  const [loaded, setLoaded] = useState(false);
  const [profile, setProfile] = useState<ProfileUpsert>({
    business_name: "",
    description: "",
    location: "",
    delivery_radius_km: null,
    accepts_delivery: false,
    accepts_pickup: true,
  });
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

  // Pre-fill every section from the server. A 404 (nothing saved yet) just
  // leaves the defaults in place — same form, first fill or later edit.
  useEffect(() => {
    Promise.all([
      profileApi.getProfile().catch(() => null),
      profileApi.getHours().catch(() => null),
      profileApi.getPolicies().catch(() => null),
    ])
      .then(([existing, existingHours, existingPolicies]) => {
        if (existing) {
          setProfile({
            business_name: existing.business_name ?? "",
            description: existing.description ?? "",
            location: existing.location ?? "",
            delivery_radius_km: existing.delivery_radius_km ?? null,
            accepts_delivery: existing.accepts_delivery,
            accepts_pickup: existing.accepts_pickup,
          });
        }
        if (existingHours && existingHours.length > 0) {
          setHours((prev) =>
            prev.map((d) => {
              const saved = existingHours.find(
                (h) => h.day_of_week === d.day_of_week,
              );
              if (!saved) return d;
              return {
                day_of_week: saved.day_of_week,
                // Backend returns "HH:MM:SS" — slice to "HH:MM" for <input type="time">
                open_time: saved.open_time ? saved.open_time.slice(0, 5) : "08:00",
                close_time: saved.close_time
                  ? saved.close_time.slice(0, 5)
                  : "20:00",
                is_closed: saved.is_closed,
                note_ar: saved.note_ar ?? null,
              };
            }),
          );
        }
        if (existingPolicies && existingPolicies.length > 0) {
          setPolicies((prev) => {
            const updated = { ...prev };
            for (const p of existingPolicies) {
              if (p.key in updated) updated[p.key] = p.value ?? "";
            }
            return updated;
          });
        }
      })
      .finally(() => setLoaded(true));
  }, []);

  async function saveBusiness() {
    if (!profile.business_name?.trim()) throw new Error(t.required);
    await profileApi.saveProfile(profile);
    await refreshMe(); // business_name feeds setup_complete
  }

  async function saveHours() {
    await profileApi.replaceHours(hours);
  }

  async function savePolicies() {
    const entries: PolicyUpsert[] = Object.entries(policies)
      .filter(([, v]) => v.trim() !== "")
      .map(([key, value]) => ({ key, value }));
    await profileApi.savePolicies(entries);
  }

  if (!loaded) {
    return (
      <ul className="flex flex-col gap-4" aria-hidden>
        {[0, 1, 2].map((i) => (
          <li
            key={i}
            className="h-40 animate-pulse rounded-2xl border border-border bg-muted/50"
          />
        ))}
      </ul>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h2 className="text-xl font-bold text-foreground">{t.profileTitle}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{t.profileSubtitle}</p>
      </header>

      <SectionCard title={t.stepBusinessTitle} onSave={saveBusiness}>
        <BusinessFields value={profile} onChange={setProfile} />
      </SectionCard>

      {/* Products live in Inventory now — keep a clear signpost so the owner
          coming from the old wizard knows where they went. */}
      <section className="rounded-2xl border border-border bg-card p-5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold text-foreground">{t.productsCardTitle}</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              {t.productsCardBody}
              {me != null && (
                <span className="ms-1 tabular">
                  ({t.productCount}: {me.product_count})
                </span>
              )}
            </p>
          </div>
          <Link
            to="/inventory"
            className="min-h-[44px] rounded-lg px-4 py-2.5 text-sm font-medium text-primary ring-1 ring-primary/40 transition-colors hover:bg-primary/10"
          >
            {t.goToInventory}
          </Link>
        </div>
      </section>

      <SectionCard title={t.stepHoursTitle} onSave={saveHours}>
        <HoursFields hours={hours} onChange={setHours} />
      </SectionCard>

      <SectionCard title={t.stepPoliciesTitle} onSave={savePolicies}>
        <PoliciesFields policies={policies} onChange={setPolicies} />
      </SectionCard>
    </div>
  );
}

// One editable card: its own save button, busy state, inline error, and a
// short-lived "saved ✓" confirmation so the owner knows the section landed.
function SectionCard({
  title,
  onSave,
  children,
}: {
  title: string;
  onSave: () => Promise<void>;
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (savedAt == null) return;
    const timer = setTimeout(() => setSavedAt(null), 2500);
    return () => clearTimeout(timer);
  }, [savedAt]);

  async function save() {
    setError(null);
    setBusy(true);
    try {
      await onSave();
      setSavedAt(Date.now());
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.status === 0 ? t.networkError : t.errorGeneric);
      } else {
        setError(err instanceof Error ? err.message : t.errorGeneric);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-2xl border border-border bg-card p-5 shadow-sm">
      <h3 className="mb-4 font-semibold text-foreground">{title}</h3>
      {children}
      <div className="mt-4 flex items-center justify-end gap-3 border-t border-border pt-4">
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {savedAt != null && (
          <p role="status" className="text-sm font-medium text-primary">
            {t.sectionSaved}
          </p>
        )}
        <Button onClick={save} loading={busy}>
          {busy ? t.saving : t.save}
        </Button>
      </div>
    </section>
  );
}

function BusinessFields({
  value,
  onChange,
}: {
  value: ProfileUpsert;
  onChange: (v: ProfileUpsert) => void;
}) {
  const set = (patch: Partial<ProfileUpsert>) => onChange({ ...value, ...patch });
  return (
    <div className="flex flex-col gap-4">
      <Field
        name="business_name"
        label={t.businessName}
        value={value.business_name ?? ""}
        onChange={(e) => set({ business_name: e.target.value })}
      />
      {/* Description: multi-line, used by agents to describe the shop to customers */}
      <div className="flex flex-col gap-1.5 text-start">
        <label htmlFor="description" className="text-sm font-medium text-foreground">
          {`${t.businessDescription} (${t.optional})`}
        </label>
        <textarea
          id="description"
          name="description"
          rows={3}
          value={value.description ?? ""}
          onChange={(e) => set({ description: e.target.value })}
          className="rounded-lg border border-border bg-card px-3 py-2 text-base text-foreground outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/30 resize-none"
        />
      </div>
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

function HoursFields({
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

function PoliciesFields({
  policies,
  onChange,
}: {
  policies: Record<string, string>;
  onChange: (p: Record<string, string>) => void;
}) {
  const set = (key: string, value: string) => onChange({ ...policies, [key]: value });
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
