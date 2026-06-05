import { type ComponentType, type SVGProps } from "react";

import {
  ApprovalsIcon,
  BillsIcon,
  CustomersIcon,
  HomeIcon,
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
  { to: "/orders", label: t.navOrders, Icon: OrdersIcon },
  { to: "/inventory", label: t.navInventory, Icon: InventoryIcon },
  { to: "/bills", label: t.navBills, Icon: BillsIcon },
  { to: "/approvals", label: t.navReorders, Icon: ApprovalsIcon },
  { to: "/customers", label: t.navCustomers, Icon: CustomersIcon },
  { to: "/setup", label: t.navSetup, Icon: SetupIcon },
];

// Mobile bottom nav stays ≤5 items (ux bottom-nav-limit). The full sidebar now has
// seven destinations, so the bar keeps the five most day-to-day ones and drops
// Setup (a one-time wizard, reached from the setup banner) and Customers (a
// reference list, less frequent than orders/inventory/bills/approvals) — both stay
// in the desktop sidebar.
const _BOTTOM_NAV_EXCLUDE = new Set(["/setup", "/customers"]);
export const BOTTOM_NAV_ITEMS: NavItem[] = NAV_ITEMS.filter(
  (item) => !_BOTTOM_NAV_EXCLUDE.has(item.to),
);
