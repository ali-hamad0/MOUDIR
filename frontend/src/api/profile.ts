import type {
  DayHours,
  OperatingHoursRead,
  PolicyRead,
  PolicyUpsert,
  ProductResponse,
  ProductWrite,
  ProfileRead,
  ProfileUpsert,
} from "./types";

import { api } from "./client";

export const profileApi = {
  getProfile: () => api.get<ProfileRead>("/profile"),
  saveProfile: (data: ProfileUpsert) => api.put<unknown>("/profile", data),
  getHours: () => api.get<OperatingHoursRead[]>("/operating-hours"),
  replaceHours: (days: DayHours[]) =>
    api.put<unknown>("/operating-hours", { days }),
  getPolicies: () => api.get<PolicyRead[]>("/policies"),
  savePolicies: (policies: PolicyUpsert[]) =>
    api.put<unknown>("/policies", { policies }),
  listProducts: () => api.get<ProductResponse[]>("/products"),
  createProduct: (data: ProductWrite) =>
    api.post<ProductResponse>("/products", data),
};
