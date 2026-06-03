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
  // Setup gating / banner
  setupIncompleteTitle: "محلك لسا مش جاهز",
  setupIncompleteBody: "كمّل ضبط محلك تتقبل طلبات الزباين.",
  setupIncompleteCta: "كمّل الضبط",
  // Generic actions
  next: "التالي",
  back: "رجوع",
  save: "حفظ",
  saving: "عم نحفظ...",
  finish: "خلصنا",
  add: "ضيف",
  remove: "شيل",
  optional: "اختياري",

  // ---- Setup wizard ----
  wizardWelcome: "مرحبا بك في مودير — خلّينا نضبط محلك",
  wizardStep: "خطوة",
  wizardOf: "من",
  // Step 1 — business details
  stepBusinessTitle: "تفاصيل المحل",
  businessName: "اسم المحل",
  businessDescription: "وصف المحل",
  businessLocation: "الموقع",
  deliveryRadiusKm: "مدى التوصيل (كم)",
  acceptsDelivery: "منوصّل",
  acceptsPickup: "في استلام من المحل",
  // Step 2 — products
  stepProductsTitle: "المنتجات",
  productNameAr: "اسم المنتج (عربي)",
  productNameEn: "اسم المنتج (إنكليزي)",
  priceLbp: "السعر (ل.ل.)",
  priceUsd: "السعر ($)",
  unit: "الوحدة",
  category: "التصنيف",
  isAvailable: "متوفر",
  addProduct: "ضيف منتج",
  noProductsYet: "ما في منتجات بعد. ضيف أول منتج.",
  atLeastOneProduct: "لازم تضيف منتج واحد على الأقل.",
  // Step 3 — operating hours
  stepHoursTitle: "أوقات الدوام",
  closed: "مسكّر",
  openTime: "من",
  closeTime: "لـ",
  ramadanNote: "ملاحظة (مثلاً دوام رمضان)",
  // Step 4 — policies
  stepPoliciesTitle: "سياسات المحل",
  minOrderLbp: "الحد الأدنى للطلب (ل.ل.)",
  deliveryFeeLbp: "رسوم التوصيل (ل.ل.)",
  deliveryZones: "مناطق التوصيل",
  paymentMethods: "طرق الدفع",
  // Days of the week (0=Mon ... 6=Sun)
  dayMon: "الإثنين",
  dayTue: "الثلاثاء",
  dayWed: "الأربعاء",
  dayThu: "الخميس",
  dayFri: "الجمعة",
  daySat: "السبت",
  daySun: "الأحد",
} as const;

export type TranslationKey = keyof typeof t;

/** Display a plan tier in Arabic, falling back to the raw value. */
export function planLabel(tier: string): string {
  if (tier === "free") return t.planFree;
  if (tier === "pro") return t.planPro;
  return tier;
}

/** Day-of-week labels indexed 0=Mon ... 6=Sun (matches the backend's enum). */
export const DAY_LABELS = [
  t.dayMon,
  t.dayTue,
  t.dayWed,
  t.dayThu,
  t.dayFri,
  t.daySat,
  t.daySun,
] as const;
