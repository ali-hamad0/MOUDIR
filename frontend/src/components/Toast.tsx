import { useEffect } from "react";

export type ToastKind = "success" | "error";

export interface ToastState {
  kind: ToastKind;
  message: string;
}

// A transient, self-dismissing notice anchored bottom-center (clear of the
// mobile bottom nav). aria-live so screen readers announce it; color is paired
// with a leading dot, never used alone (a11y color-not-only).
export function Toast({
  toast,
  onDismiss,
  durationMs = 3500,
}: {
  toast: ToastState | null;
  onDismiss: () => void;
  durationMs?: number;
}) {
  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(onDismiss, durationMs);
    return () => clearTimeout(id);
  }, [toast, durationMs, onDismiss]);

  if (!toast) return null;
  const cls =
    toast.kind === "success"
      ? "border-accent/30 bg-card text-foreground"
      : "border-destructive/30 bg-card text-destructive";
  const dot = toast.kind === "success" ? "bg-accent" : "bg-destructive";
  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 bottom-24 z-30 flex justify-center px-4 lg:bottom-8"
    >
      <div
        className={`pointer-events-auto flex items-center gap-2 rounded-xl border px-4 py-3 text-sm font-medium shadow-lg ${cls}`}
      >
        <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${dot}`} />
        {toast.message}
      </div>
    </div>
  );
}
