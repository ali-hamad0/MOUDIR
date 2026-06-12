import { api } from "./client";

// GET /dashboard/summary (mirrors app/api/dashboard.py) — one call powering
// the home-page KPI cards + charts. All tenant-scoped by the JWT.

export interface DailyPoint {
  day: string; // ISO date (Beirut calendar day)
  orders: number;
  revenue_lbp: number;
}

export interface TopProduct {
  name: string;
  units: number;
}

export interface StatusCount {
  status: string;
  count: number;
}

export interface DashboardSummary {
  daily: DailyPoint[]; // trailing 14 days, gap-filled, oldest first
  today_orders: number;
  today_revenue_lbp: number;
  top_products: TopProduct[];
  order_status: StatusCount[];
  low_stock_count: number;
  total_products: number;
}

export const dashboardApi = {
  summary: () => api.get<DashboardSummary>("/dashboard/summary"),
};
