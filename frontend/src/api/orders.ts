import { api } from "./client";
import type { OrdersPage } from "./types";

export const ordersApi = {
  today: (limit = 50, offset = 0) =>
    api.get<OrdersPage>(`/orders/today?limit=${limit}&offset=${offset}`),
};
