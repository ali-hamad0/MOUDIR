import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server runs on 5173 (the origin the backend's CORS allowlist defaults
// to — see Settings.cors_allow_origins). `host: true` lets it be reached from a
// phone on the LAN for real 360px testing.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
  },
});
