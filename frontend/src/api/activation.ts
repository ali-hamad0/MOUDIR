import { API_BASE_URL } from "../config";
import { ApiError } from "./client";

// Public activation endpoints (no auth — the one-time token is the credential).

export interface ActivationCheck {
  valid: boolean;
  email: string | null;
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const activationApi = {
  check: (token: string) =>
    call<ActivationCheck>("GET", `/activate?token=${encodeURIComponent(token)}`),
  activate: (token: string, newPassword: string) =>
    call<{ message: string }>("POST", "/activate", {
      token,
      new_password: newPassword,
    }),
};
