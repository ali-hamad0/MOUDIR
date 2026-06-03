import { type InputHTMLAttributes } from "react";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  error?: string | null;
}

// Labeled input (visible label, not placeholder-only — ux input-labels), with
// the error shown below the field (ux error-placement). 44px+ tall for touch.
export function Field({ label, error, id, ...props }: FieldProps) {
  const inputId = id ?? props.name;
  const errorId = error ? `${inputId}-error` : undefined;
  return (
    <div className="flex flex-col gap-1.5 text-start">
      <label htmlFor={inputId} className="text-sm font-medium text-foreground">
        {label}
      </label>
      <input
        id={inputId}
        aria-invalid={Boolean(error)}
        aria-describedby={errorId}
        className={`min-h-[44px] rounded-lg border bg-card px-3 text-base text-foreground outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/30 ${
          error ? "border-destructive" : "border-border"
        }`}
        {...props}
      />
      {error && (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
