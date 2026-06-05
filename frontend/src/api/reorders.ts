import { api } from "./client";
import type { ApprovalRead, ApprovalsPage } from "./types";

// The owner-facing HIL inbox for reorder purchase orders. Distinct from the
// founder admin approvals (api/admin.ts) — this runs behind the owner JWT and
// drives /approvals on the backend.
export const reordersApi = {
  list: (limit = 50, offset = 0) =>
    api.get<ApprovalsPage>(`/approvals?limit=${limit}&offset=${offset}`),
  // note is optional; the dispatch token is minted server-side, never sent here.
  approve: (poId: string, note?: string) =>
    api.post<ApprovalRead>(`/approvals/${poId}/approve`, { note: note ?? null }),
  // reason is required — the backend 422s without it.
  reject: (poId: string, reason: string) =>
    api.post<ApprovalRead>(`/approvals/${poId}/reject`, { reason }),
  // Manually close a dispatch_failed PO the owner sent out of band.
  markSent: (poId: string) => api.post<ApprovalRead>(`/approvals/${poId}/mark-sent`),
};
