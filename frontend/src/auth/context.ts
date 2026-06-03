import { createContext, useContext } from "react";

import type { LoginRequest, Me } from "../api/types";

export interface AuthState {
  token: string | null;
  me: Me | null;
  loading: boolean; // true while restoring a session / fetching /me
  login: (creds: LoginRequest) => Promise<void>;
  logout: () => void;
  refreshMe: () => Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
