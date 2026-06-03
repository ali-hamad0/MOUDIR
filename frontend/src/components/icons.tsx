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

export function LogoutIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4" />
      <path d="M10 12H3" />
      <path d="M6 9l-3 3 3 3" />
    </svg>
  );
}
