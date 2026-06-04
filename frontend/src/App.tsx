import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AppShell } from "./components/AppShell";
import ActivatePage from "./pages/ActivatePage";
import AdminLoginPage from "./pages/AdminLoginPage";
import ApprovalsPage from "./pages/ApprovalsPage";
import CustomersPage from "./pages/CustomersPage";
import DashboardPage from "./pages/DashboardPage";
import LoginPage from "./pages/LoginPage";
import OrdersPage from "./pages/OrdersPage";
import SetupWizard from "./pages/SetupWizard";
import SignupPage from "./pages/SignupPage";

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
          {/* Authenticated owner area: the shell is a layout route; pages render
              in its <Outlet>. */}
          <Route
            element={
              <ProtectedRoute>
                <AppShell />
              </ProtectedRoute>
            }
          >
            <Route path="/" element={<DashboardPage />} />
            <Route path="/orders" element={<OrdersPage />} />
            <Route path="/customers" element={<CustomersPage />} />
            <Route path="/setup" element={<SetupWizard />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
