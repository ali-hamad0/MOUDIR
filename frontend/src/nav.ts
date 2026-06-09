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
];

// Mobile bottom nav stays ≤5 items (ux bottom-nav-limit). The full sidebar has nine
// destinations, so the bar keeps the five most day-to-day ones and drops Setup (a
// one-time wizard), Customers, Bills, and Insights — all four stay in the desktop sidebar.
const _BOTTOM_NAV_EXCLUDE = new Set(["/setup", "/customers", "/bills", "/insights", "/costs"]);
export const BOTTOM_NAV_ITEMS: NavItem[] = NAV_ITEMS.filter(
  (item) => !_BOTTOM_NAV_EXCLUDE.has(item.to),
);
