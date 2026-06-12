import { API_BASE_URL } from "../config";
import { ApiError } from "./client";
import type { DailyCostResponse } from "./types";

// Admin (founder) API. Kept SEPARATE from the owner api client: the founder uses
// a distinct token and a distinct localStorage key so the two identities never
// share session state. The backend also enforces this (admin vs access tokens).

const ADMIN_TOKEN_KEY = "modir.adminToken";

export function getAdminToken(): string | null {
  return localStorage.getItem(ADMIN_TOKEN_KEY);
}
export function setAdminToken(token: string | null): void {
  if (token) localStorage.setItem(ADMIN_TOKEN_KEY, token);
  else localStorage.removeItem(ADMIN_TOKEN_KEY);
}

async function adminRequest<T>(
  method: string,
  path: string,
  body?: unknown,
  withAuth = true,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (withAuth) {
    const tok = getAdminToken();
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
  }
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface AdminTokenResponse {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
}

export interface SignupRequestAdminView {
  id: string;
  business_name: string;
  owner_phone: string;
  owner_email: string;
  status: string;
  created_at: string;
  reviewed_at: string | null;
  reject_reason: string | null;
  provisioned_tenant_id: string | null;
}

export interface TenantAdminView {
  id: string;
  name: string;
  whatsapp_number: string;
  plan_tier: string;
  is_active: boolean;
  data_source: string | null;
  created_at: string;
  // Billing: raw stored fields; past_due/expired is derived client-side from
  // current_period_end (mirrors services/billing.py).
  subscription_status: string;
  current_period_end: string | null;
}

export interface PaymentView {
  id: string;
  amount_usd: string;
  method: string;
  months: number;
  note: string | null;
  plan_tier: string;
  period_end_after: string;
  created_at: string;
}

export interface PaymentRecordResponse {
  payment: PaymentView;
  plan_tier: string;
  subscription_status: string;
  current_period_end: string;
}

export interface TenantDetailAdminView extends TenantAdminView {
  customers_count: number;
  orders_today: number;
}

export const adminApi = {
  login: (email: string, password: string) =>
    adminRequest<AdminTokenResponse>("POST", "/admin/login", { email, password }, false),
  listRequests: (status?: string) =>
    adminRequest<SignupRequestAdminView[]>(
      "GET",
      `/admin/signup-requests${status ? `?status_filter=${status}` : ""}`,
    ),
  approve: (id: string, whatsapp_number: string) =>
    adminRequest<unknown>("POST", `/admin/signup-requests/${id}/approve`, {
      whatsapp_number,
    }),
  reject: (id: string, reason: string) =>
    adminRequest<unknown>("POST", `/admin/signup-requests/${id}/reject`, { reason }),
  listTenants: () => adminRequest<TenantAdminView[]>("GET", "/admin/tenants"),
  tenantDetail: (id: string) =>
    adminRequest<TenantDetailAdminView>("GET", `/admin/tenants/${id}`),
  tenantCosts: (id: string) =>
    adminRequest<DailyCostResponse>("GET", `/admin/tenants/${id}/costs`),
  suspendTenant: (id: string, reason: string) =>
    adminRequest<unknown>("POST", `/admin/tenants/${id}/suspend`, { reason }),
  reactivateTenant: (id: string, reason: string) =>
    adminRequest<unknown>("POST", `/admin/tenants/${id}/reactivate`, { reason }),
  // Billing (Phase 11): manual payments + plan changes.
  setTenantPlan: (id: string, plan_tier: string) =>
    adminRequest<TenantAdminView>("POST", `/admin/tenants/${id}/plan`, { plan_tier }),
  // Founder override: set plan + paid-through directly (fix / grant / reset).
  overrideSubscription: (
    id: string,
    payload: { plan_tier: string; current_period_end: string | null },
  ) => adminRequest<TenantAdminView>("PUT", `/admin/tenants/${id}/subscription`, payload),
  recordPayment: (
    id: string,
    payload: {
      amount_usd: string;
      method: string;
      months: number;
      note?: string | null;
      plan_tier?: string | null;
    },
  ) => adminRequest<PaymentRecordResponse>("POST", `/admin/tenants/${id}/payments`, payload),
  listPayments: (id: string) =>
    adminRequest<PaymentView[]>("GET", `/admin/tenants/${id}/payments`),
};
