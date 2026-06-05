import { api } from "./client";
import type {
  InventoryPage,
  InventoryUpsert,
  InventoryRead,
  SupplierRead,
} from "./types";

export const inventoryApi = {
  list: (limit = 50, offset = 0) =>
    api.get<InventoryPage>(`/inventory?limit=${limit}&offset=${offset}`),
  // Upsert the inventory row for a product the tenant owns (404 if not theirs).
  upsert: (productId: string, body: InventoryUpsert) =>
    api.put<InventoryRead>(`/inventory/${productId}`, body),
  suppliers: () => api.get<SupplierRead[]>("/suppliers"),
};
