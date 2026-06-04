import { API_BASE_URL } from "../config";
import { ApiError } from "./client";

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
};
