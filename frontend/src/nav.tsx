import { type ComponentType, type SVGProps } from "react";

import {
  CustomersIcon,
  HomeIcon,
  OrdersIcon,
  SetupIcon,
} from "./components/icons";
import { t } from "./i18n";

export interface NavItem {
  to: string;
  label: string;
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
}

// Primary navigation, shared by the sidebar (desktop) and bottom nav (mobile).
// Bottom nav stays ≤5 items (ux bottom-nav-limit). Order: most-used first.
export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: t.navHome, Icon: HomeIcon },
  { to: "/orders", label: t.navOrders, Icon: OrdersIcon },
  { to: "/customers", label: t.navCustomers, Icon: CustomersIcon },
  { to: "/setup", label: t.navSetup, Icon: SetupIcon },
];
