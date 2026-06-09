import { api } from "./client";
import type { DailyCostResponse } from "./types";

export const costsApi = {
  dashboard: () => api.get<DailyCostResponse>("/dashboard/costs"),
};
