interface ToggleProps {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}

// Labeled switch. Uses a real checkbox for a11y/keyboard; the visual track is
// 44px-tall tappable. RTL-aware via logical positioning.
export function Toggle({ label, checked, onChange, disabled }: ToggleProps) {
  return (
    <label className="flex min-h-[44px] cursor-pointer items-center justify-between gap-3">
      <span className="text-sm font-medium text-foreground">{label}</span>
      <input
        type="checkbox"
        className="peer sr-only"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      {/* Track + knob. The knob sits at the start edge and slides to the end
          when checked; `justify-*` is direction-aware so it works in RTL. */}
      <span
        aria-hidden
        className={`flex h-6 w-11 items-center rounded-full p-0.5 transition-colors peer-focus-visible:ring-2 peer-focus-visible:ring-primary/40 ${
          checked ? "justify-end bg-accent" : "justify-start bg-border"
        }`}
      >
        <span className="h-5 w-5 rounded-full bg-card shadow" />
      </span>
    </label>
  );
}
