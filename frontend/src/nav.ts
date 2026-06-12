import { type ComponentType, type SVGProps } from "react";

import {
  ApprovalsIcon,
  BillsIcon,
  ChatIcon,
  CostIcon,
  CustomersIcon,
  HomeIcon,
  InsightsIcon,
  InventoryIcon,
  OrdersIcon,
  SetupIcon,
  StarIcon,
} from "./components/icons";
import { t } from "./i18n";

export interface NavItem {
  to: string;
  label: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
}

// Full navigation, shown in the desktop sidebar. Order: most-used first.
export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: t.navHome, Icon: HomeIcon },
  { to: "/chat", label: t.navChat, Icon: ChatIcon },
  { to: "/orders", label: t.navOrders, Icon: OrdersIcon },
  { to: "/inventory", label: t.navInventory, Icon: InventoryIcon },
  { to: "/bills", label: t.navBills, Icon: BillsIcon },
  { to: "/approvals", label: t.navReorders, Icon: ApprovalsIcon },
  { to: "/customers", label: t.navCustomers, Icon: CustomersIcon },
  { to: "/insights", label: t.navInsights, Icon: InsightsIcon },
  { to: "/costs", label: t.navCosts, Icon: CostIcon },
  { to: "/setup", label: t.navSetup, Icon: SetupIcon },
  { to: "/upgrade", label: t.navUpgrade, Icon: StarIcon },
];

// Mobile bottom nav stays ≤5 items (ux bottom-nav-limit): the four most
// day-to-day destinations get a slot, and the fifth slot is the "More" sheet
// (rendered by AppShell) holding everything else — so every sidebar page stays
// reachable on a phone (ux persistent-nav). New NAV_ITEMS entries land in the
// sheet automatically.
const _BOTTOM_NAV_QUICK = new Set(["/", "/chat", "/orders", "/inventory"]);
export const BOTTOM_NAV_ITEMS: NavItem[] = NAV_ITEMS.filter((item) =>
  _BOTTOM_NAV_QUICK.has(item.to),
);
export const BOTTOM_NAV_MORE_ITEMS: NavItem[] = NAV_ITEMS.filter(
  (item) => !_BOTTOM_NAV_QUICK.has(item.to),
);
