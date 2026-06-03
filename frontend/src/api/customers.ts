import { api } from "./client";
import type { CustomersPage } from "./types";

export const customersApi = {
  list: (limit = 50, offset = 0) =>
    api.get<CustomersPage>(`/customers?limit=${limit}&offset=${offset}`),
};
