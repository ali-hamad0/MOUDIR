// Lebanese-Arabic UI strings. One dictionary, referenced by key — no hardcoded
// literals scattered in components (the frontend mirror of the backend prompts/
// rule). English keys double as the fallback/identifier.

export const t = {
  appName: "مودير",
  tagline: "مساعد الأعمال الذكي للمحلات اللبنانية",
  // Auth
  loginTitle: "تسجيل الدخول",
  loginSubtitle: "ادخل على لوحة محلّك",
  whatsappNumber: "رقم الواتساب تبع المحل",
  email: "الإيميل",
  password: "كلمة السر",
  loginButton: "دخول",
  loggingIn: "عم ندخّلك...",
  // Same vague message for every failure — never leak which field was wrong.
  invalidCredentials: "في خطأ بالمعلومات. جرّب مرة تانية.",
  networkError: "ما قدرنا نتواصل مع السيرفر. تأكد من الإنترنت وجرّب مرة تانية.",
  logout: "خروج",
  loading: "عم نحمّل...",
  required: "هالخانة ضرورية",
  // Navigation
  navHome: "الرئيسية",
  navOrders: "الطلبات",
  navCustomers: "الزباين",
  navSetup: "ضبط المحل",
  // App shell / whoami
  shop: "المحل",
  plan: "الباقة",
  productCount: "عدد المنتجات",
  // Plan tiers (display)
  planFree: "مجاني",
  planPro: "مدفوع",
  // Generic states
  retry: "جرّب مرة تانية",
  errorGeneric: "صار خطأ. جرّب مرة تانية.",
} as const;

export type TranslationKey = keyof typeof t;

/** Display a plan tier in Arabic, falling back to the raw value. */
export function planLabel(tier: string): string {
  if (tier === "free") return t.planFree;
  if (tier === "pro") return t.planPro;
  return tier;
}
