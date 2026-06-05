import type {
  DayHours,
  PolicyUpsert,
  ProductResponse,
  ProductWrite,
  ProfileUpsert,
} from "./types";

import { api } from "./client";

// Thin wrappers over the existing Phase 1 profile endpoints. The wizard drives
// these — there is almost no new backend (ProfileService already queues the
// knowledge_base_docs rows as `pending` on each write).
export const profileApi = {
  saveProfile: (data: ProfileUpsert) => api.put<unknown>("/profile", data),
  // The tenant's full catalog — the bill-review product picker (Task 5.18).
  listProducts: () => api.get<ProductResponse[]>("/products"),
  createProduct: (data: ProductWrite) =>
    api.post<ProductResponse>("/products", data),
  replaceHours: (days: DayHours[]) =>
    api.put<unknown>("/operating-hours", { days }),
  savePolicies: (policies: PolicyUpsert[]) =>
    api.put<unknown>("/policies", { policies }),
};
