import { type ComponentType, type SVGProps } from "react";

import {
  ApprovalsIcon,
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
  { to: "/approvals", label: t.navReorders, Icon: ApprovalsIcon },
  { to: "/customers", label: t.navCustomers, Icon: CustomersIcon },
  { to: "/setup", label: t.navSetup, Icon: SetupIcon },
];

// Mobile bottom nav stays ≤5 items (ux bottom-nav-limit). Setup is the least
// day-to-day destination (a one-time wizard, also reached from the setup banner),
// so it's the one dropped from the bar — still available in the desktop sidebar.
export const BOTTOM_NAV_ITEMS: NavItem[] = NAV_ITEMS.filter(
  (item) => item.to !== "/setup",
);
