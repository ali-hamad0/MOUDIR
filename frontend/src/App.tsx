import { API_BASE_URL } from "./config";

// Task 3.6 proof-of-concept: proves the toolchain, the Arabic font, RTL
// direction, the design tokens, and the API-base-URL wiring all work together at
// 360px. Real screens (login, shell, wizard, feed) arrive in 3.7+.
export default function App() {
  return (
    <main className="min-h-dvh bg-background text-foreground">
      <div className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center gap-6 px-4 text-center">
        <span className="rounded-full bg-primary px-4 py-1 text-sm font-medium text-on-primary">
          لوحة التحكم
        </span>
        <h1 className="font-sans text-5xl font-bold text-primary">مودير</h1>
        <p className="text-muted-foreground">
          مساعد الأعمال الذكي للمحلات اللبنانية
        </p>
        <div className="w-full rounded-xl border border-border bg-card p-4 text-start shadow-sm">
          <p className="text-sm text-muted-foreground">عنوان الـ API</p>
          <p className="tabular mt-1 break-all text-sm">{API_BASE_URL}</p>
        </div>
      </div>
    </main>
  );
}
