import { type SVGProps } from "react";

// SVG icons (never emoji — ui-ux-pro-max no-emoji-icons), one consistent
// stroke-based set (Lucide-style, 1.75 stroke). currentColor so they theme.
type IconProps = SVGProps<SVGSVGElement>;

const base: IconProps = {
  width: 24,
  height: 24,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.75,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
};

export function HomeIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 9.5V21h14V9.5" />
    </svg>
  );
}

export function OrdersIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M6 2h9l5 5v15H6z" />
      <path d="M14 2v6h6" />
      <path d="M9 13h6M9 17h6" />
    </svg>
  );
}

export function CustomersIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
      <path d="M16 6.5a3 3 0 0 1 0 5.8" />
      <path d="M20.5 20a5 5 0 0 0-3.5-4.8" />
    </svg>
  );
}

export function SetupIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M4 7h10M4 12h16M4 17h7" />
      <circle cx="18" cy="7" r="2" />
      <circle cx="14" cy="17" r="2" />
    </svg>
  );
}

export function InventoryIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z" />
      <path d="M3 7.5 12 12l9-4.5" />
      <path d="M12 12v9" />
    </svg>
  );
}

// Low-stock indicator — a triangle warning, paired with the Arabic label so the
// badge never relies on color alone (a11y color-not-only, Phase 3 rule).
export function AlertIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M10.3 3.6 2.5 17a2 2 0 0 0 1.7 3h15.6a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </svg>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

// Owner approvals inbox — a clipboard with a check (a human-gated reorder).
export function ApprovalsIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M9 4h6v3H9z" />
      <path d="M9 5.5H6.5A1.5 1.5 0 0 0 5 7v12.5A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V7a1.5 1.5 0 0 0-1.5-1.5H15" />
      <path d="m9 13.5 2 2 4-4" />
    </svg>
  );
}

// Supplier bills — a receipt/document with lines (OCR'd paper bill).
export function BillsIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M6 3h12v18l-3-1.5L12 21l-3-1.5L6 21z" />
      <path d="M9 8h6" />
      <path d="M9 12h6" />
    </svg>
  );
}

// Insights — a line chart trending up (the ML predictions panel).
export function InsightsIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M4 4v15a1 1 0 0 0 1 1h15" />
      <path d="m7 14 3.5-4 3 2.5L20 6" />
    </svg>
  );
}

// Owner chat panel — a speech bubble for the WhatsApp-style chat UI.
export function ChatIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
  );
}

export function CostIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 6v2" />
      <path d="M12 16v2" />
      <path d="M9 9h1a2 2 0 0 1 0 4h-1a2 2 0 0 0 0 4h3" />
    </svg>
  );
}

// Pro-locked feature marker (padlock).
export function LockIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  );
}

// Upgrade / Pro plan marker (sparkle-star).
export function StarIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="m12 3 2.3 5.4 5.7.5-4.3 3.9 1.3 5.7L12 15.6 7 18.5l1.3-5.7L4 8.9l5.7-.5z" />
    </svg>
  );
}

// Bottom-nav overflow ("More") — three dots, Lucide more-horizontal style.
export function MoreIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="5" cy="12" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="19" cy="12" r="1" />
    </svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}

export function GlobeIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3a13.5 13.5 0 0 1 0 18a13.5 13.5 0 0 1 0-18" />
    </svg>
  );
}

export function LogoutIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4" />
      <path d="M10 12H3" />
      <path d="M6 9l-3 3 3 3" />
    </svg>
  );
}
