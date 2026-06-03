// The API base URL is read from a Vite env var (VITE_API_BASE_URL) with a dev
// fallback — never hardcoded. Set it per-environment in .env / deployment.
export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
