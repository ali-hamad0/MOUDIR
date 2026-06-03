import { type ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { t } from "../i18n";
import { useAuth } from "./context";

// Gate for authenticated pages. While a restored session is still loading /me,
// show a neutral loading state; with no token, redirect to /login.
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { token, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center text-muted-foreground">
        {t.loading}
      </div>
    );
  }
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
