import { type ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  loading?: boolean;
}

// Primary button. Disabled + spinner while async (ux loading-buttons); 44px+
// touch target; reduced opacity when disabled (ux disabled-states); subtle
// scale on press for touch feedback (ux scale-feedback — transform only, so no
// layout shift).
export function Button({
  loading,
  disabled,
  children,
  className = "",
  ...props
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={`inline-flex min-h-[44px] items-center justify-center gap-2 rounded-lg bg-primary px-4 font-medium text-on-primary transition-[opacity,transform] hover:opacity-90 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
      {...props}
    >
      {loading && (
        <span
          aria-hidden
          className="h-4 w-4 animate-spin rounded-full border-2 border-on-primary/40 border-t-on-primary"
        />
      )}
      {children}
    </button>
  );
}
