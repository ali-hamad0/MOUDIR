import { API_BASE_URL } from "../config";

/**
 * Thin fetch wrapper for the Modir API.
 *
 * - Reads the base URL from VITE_API_BASE_URL (config.ts), never hardcoded.
 * - Attaches the JWT as `Authorization: Bearer` when a token getter is set.
 * - Centralizes 401 handling: any 401 fires `onUnauthorized` (the AuthProvider
 *   wires this to clear the token + redirect to /login) so no screen has to
 *   handle expired sessions itself.
 */

let getToken: () => string | null = () => null;
let onUnauthorized: () => void = () => {};

export function configureClient(opts: {
  getToken: () => string | null;
  onUnauthorized: () => void;
}): void {
  getToken = opts.getToken;
  onUnauthorized = opts.onUnauthorized;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    // Network/CORS failure — surface a typed error the UI can show + retry.
    throw new ApiError(0, "network");
  }

  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError(401, "unauthorized");
  }

  if (!res.ok) {
    // FastAPI puts the message in `detail`; fall back to the status text.
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* non-JSON error body — keep the status text */
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/**
 * Multipart upload (e.g. a supplier-bill image). Sends FormData WITHOUT a
 * Content-Type header so the browser sets the multipart boundary itself; the JWT
 * + 401 handling are the same as `request`.
 */
async function upload<T>(path: string, form: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: "POST",
      headers,
      body: form,
    });
  } catch {
    throw new ApiError(0, "network");
  }

  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError(401, "unauthorized");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => upload<T>(path, form),
};
