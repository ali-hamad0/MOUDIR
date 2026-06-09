import { api } from "./client";
import type { ManualOrderRequest, OrderRead, OrdersPage } from "./types";

export const ordersApi = {
  today: (limit = 50, offset = 0) =>
    api.get<OrdersPage>(`/orders/today?limit=${limit}&offset=${offset}`),
  // Mark a confirmed order completed — the trigger for inventory deduction and
  // (server-side) low-stock reorder drafting. 409 if it isn't completable.
  complete: (orderId: string) =>
    api.post<OrderRead>(`/orders/${orderId}/complete`),
  // Phase 8 (Task 8.5): Dashboard owner creates an order manually (AI down).
  createManual: (body: ManualOrderRequest) =>
    api.post<OrderRead>("/orders/manual", body),
};
