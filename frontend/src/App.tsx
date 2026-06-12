import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppShell } from "./components/AppShell";
import ActivatePage from "./pages/ActivatePage";
import AdminLoginPage from "./pages/AdminLoginPage";
import AdminTenantsPage from "./pages/AdminTenantsPage";
import ApprovalsPage from "./pages/ApprovalsPage";
import BillReviewPage from "./pages/BillReviewPage";
import BillsPage from "./pages/BillsPage";
import CustomersPage from "./pages/CustomersPage";
import InsightsPage from "./pages/InsightsPage";
import CostDashboardPage from "./pages/CostDashboardPage";
import ManualOrderPage from "./pages/ManualOrderPage";
import InventoryPage from "./pages/InventoryPage";
import LoginPage from "./pages/LoginPage";
import OrdersPage from "./pages/OrdersPage";
import OwnerChatPage from "./pages/OwnerChatPage";
import ReordersPage from "./pages/ReordersPage";
import BillingResultPage from "./pages/BillingResultPage";
import ShopProfilePage from "./pages/ShopProfilePage";
import SignupPage from "./pages/SignupPage";
import UpgradePage from "./pages/UpgradePage";

// The dashboard pulls in recharts (~the bulk of the bundle); lazy-loading it
// keeps login and every other screen off that cost on slow mobile networks
// (ux bundle-splitting). The fallback mirrors the page's own skeletons.
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
// The founder overview shares the recharts chunk — same lazy treatment.
const AdminOverviewPage = lazy(() => import("./pages/AdminOverviewPage"));

function DashboardFallback() {
  return (
    <div className="flex flex-col gap-4" aria-hidden>
      <div className="h-7 w-32 animate-pulse rounded-lg bg-muted/50" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded-2xl border border-border bg-muted/50" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {[0, 1].map((i) => (
          <div key={i} className="h-64 animate-pulse rounded-2xl border border-border bg-muted/50" />
        ))}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          {/* Public signup request (prospective owner applies — no account yet). */}
          <Route path="/signup" element={<SignupPage />} />
          {/* Public activation (owner sets password from the email link). */}
          <Route path="/activate" element={<ActivatePage />} />
          {/* Founder admin area — separate identity, separate token. */}
          <Route path="/admin/login" element={<AdminLoginPage />} />
          <Route path="/admin" element={<ApprovalsPage />} />
          <Route
            path="/admin/overview"
            element={
              <Suspense fallback={<DashboardFallback />}>
                <AdminOverviewPage />
              </Suspense>
            }
          />
          <Route path="/admin/tenants" element={<AdminTenantsPage />} />
          {/* Authenticated owner area: the shell is a layout route; pages render
              in its <Outlet>. */}
          <Route
            element={
              <ProtectedRoute>
                <AppShell />
              </ProtectedRoute>
            }
          >
            <Route
              path="/"
              element={
                <Suspense fallback={<DashboardFallback />}>
                  <DashboardPage />
                </Suspense>
              }
            />
            <Route path="/chat" element={<OwnerChatPage />} />
            <Route path="/orders" element={<OrdersPage />} />
            <Route path="/inventory" element={<InventoryPage />} />
            <Route path="/bills" element={<BillsPage />} />
            <Route path="/bills/:billId" element={<BillReviewPage />} />
            <Route path="/approvals" element={<ReordersPage />} />
            <Route path="/customers" element={<CustomersPage />} />
            <Route path="/insights" element={<InsightsPage />} />
            <Route path="/setup" element={<ShopProfilePage />} />
            <Route path="/orders/manual" element={<ManualOrderPage />} />
            <Route path="/costs" element={<CostDashboardPage />} />
            <Route path="/upgrade" element={<UpgradePage />} />
            <Route path="/billing/result" element={<BillingResultPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
