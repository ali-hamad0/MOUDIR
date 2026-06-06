import { api } from "./client";
import type {
  AnomalyPredictions,
  ChurnPredictions,
  DemandPredictions,
} from "./types";

// Read-only ML predictions (Phase 6, Task 6.10). All tenant-scoped by the JWT —
// no tenant_id in any path. Served from the lifespan-loaded predictors (stub by
// default → null values, trained → real numbers).
export const predictionsApi = {
  demand: () => api.get<DemandPredictions>("/predictions/demand"),
  churn: () => api.get<ChurnPredictions>("/predictions/churn"),
  anomaly: (window = 14) =>
    api.get<AnomalyPredictions>(`/predictions/anomaly?window=${window}`),
};
