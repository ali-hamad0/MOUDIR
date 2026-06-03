/** @type {import('tailwindcss').Config} */
// Tokens map to the CSS variables defined in src/index.css. Components reference
// semantic names (bg-card, text-foreground, ...), never raw hex — so light/dark
// and theming stay token-driven. Tailwind honors logical properties (ms-*/me-*,
// ps-*/pe-*, text-start/text-end) under dir="rtl"; we use those, never ml/mr.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: "var(--color-primary)",
        "on-primary": "var(--color-on-primary)",
        accent: "var(--color-accent)",
        "on-accent": "var(--color-on-accent)",
        background: "var(--color-background)",
        foreground: "var(--color-foreground)",
        card: "var(--color-card)",
        muted: "var(--color-muted)",
        "muted-foreground": "var(--color-muted-foreground)",
        border: "var(--color-border)",
        destructive: "var(--color-destructive)",
        // Order-status semantics (always paired with an icon/label in the UI,
        // never color alone — a11y color-not-only).
        "status-confirmed": "var(--color-accent)",
        "status-pending": "var(--color-amber)",
        "status-issue": "var(--color-destructive)",
      },
      fontFamily: {
        // Arabic-first UI font, with Inter for Latin/numbers as fallback.
        sans: ["Cairo", "Inter", "system-ui", "sans-serif"],
        // Tabular numbers for money/quantities so columns don't jitter.
        numeric: ["Inter", "Cairo", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
