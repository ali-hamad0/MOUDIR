import { api } from "./client";

// Owner billing (Phase 11): online Pro checkout through Whish Pay.
// startCheckout returns the Whish-hosted payment URL to redirect to; the
// result page then polls checkoutStatus, which verifies server-side and
// activates Pro only on a confirmed payment.

export interface CheckoutOut {
  checkout_id: string;
  collect_url: string;
}

export interface CheckoutStatusOut {
  status: "pending" | "paid" | "failed";
  plan_tier: string;
  subscription_status: string;
  current_period_end: string | null;
}

export const billingApi = {
  startCheckout: (months: number) =>
    api.post<CheckoutOut>("/billing/checkout", { months }),
  checkoutStatus: (checkoutId: string) =>
    api.get<CheckoutStatusOut>(`/billing/checkout/${checkoutId}`),
};
