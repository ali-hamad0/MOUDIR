import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api, ApiError, configureClient } from "../api/client";
import type { LoginRequest, Me, TokenResponse } from "../api/types";
import { AuthContext, type AuthState } from "./context";

const TOKEN_KEY = "modir.token";

export function AuthProvider({ children }: { children: ReactNode }) {
  // Token in memory + localStorage so a hard refresh keeps the session.
  const tokenRef = useRef<string | null>(localStorage.getItem(TOKEN_KEY));
  const [token, setTokenState] = useState<string | null>(tokenRef.current);
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(tokenRef.current));

  const setToken = useCallback((value: string | null) => {
    tokenRef.current = value;
    setTokenState(value);
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setMe(null);
  }, [setToken]);

  // Wire the API client once: it reads the token from the ref (always current)
  // and calls logout on any 401 (expired/invalid session → back to /login).
  configureClient({
    getToken: () => tokenRef.current,
    onUnauthorized: logout,
  });

  const refreshMe = useCallback(async () => {
    if (!tokenRef.current) return;
    const data = await api.get<Me>("/me");
    setMe(data);
  }, []);

  const login = useCallback(
    async (creds: LoginRequest) => {
      const res = await api.post<TokenResponse>("/auth/login", creds);
      setToken(res.access_token);
      const data = await api.get<Me>("/me");
      setMe(data);
    },
    [setToken],
  );

  // On mount with a restored token, fetch /me. A 401 here triggers logout via
  // the client's onUnauthorized, so a stale token cleanly lands on /login.
  useEffect(() => {
    let cancelled = false;
    if (!tokenRef.current) {
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const data = await api.get<Me>("/me");
        if (!cancelled) setMe(data);
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 401) {
          // Non-auth error (e.g. network): keep the token, let screens retry.
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<AuthState>(
    () => ({ token, me, loading, login, logout, refreshMe }),
    [token, me, loading, login, logout, refreshMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
