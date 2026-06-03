// Lebanese-Arabic UI strings. One dictionary, referenced by key — no hardcoded
// literals scattered in components (the frontend mirror of the backend prompts/
// rule). English keys double as the fallback/identifier. Task 3.8 extends this
// with the full app-shell vocabulary.

export const t = {
  appName: "مودير",
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
} as const;

export type TranslationKey = keyof typeof t;
