import { api } from "./client";
import type {
  BillDetail,
  BillLineUpdate,
  BillsPage,
  BillUploadAccepted,
} from "./types";

// Supplier-bill OCR pipeline (Phase 5). Upload streams the photo to MinIO and
// returns 202; the worker OCRs it. The review endpoints (detail/edit/approve/
// reject) drive the bill-review screen (Task 5.18). tenant scope is the JWT.
export const billsApi = {
  list: (limit = 50, offset = 0) =>
    api.get<BillsPage>(`/bills?limit=${limit}&offset=${offset}`),

  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api.upload<BillUploadAccepted>("/bills", form);
  },

  get: (billId: string) => api.get<BillDetail>(`/bills/${billId}`),

  updateLines: (billId: string, lines: BillLineUpdate[]) =>
    api.put<BillDetail>(`/bills/${billId}/lines`, { lines }),

  approve: (billId: string) => api.post<BillDetail>(`/bills/${billId}/approve`, {}),

  // reason is required — the backend 422s without it.
  reject: (billId: string, reason: string) =>
    api.post<BillDetail>(`/bills/${billId}/reject`, { reason }),
};
