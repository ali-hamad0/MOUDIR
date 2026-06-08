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
  // Public signup request
  signupTitle: "سجّل محلك بمودير",
  signupSubtitle: "إبعتلنا طلبك وفريقنا رح يراجعه ويتواصل معك.",
  signupBusinessName: "اسم المحل",
  signupOwnerPhone: "رقم تلفونك",
  signupEmail: "الإيميل",
  signupSubmit: "إبعت الطلب",
  signupSubmitting: "عم نبعت...",
  signupSuccessTitle: "وصلنا طلبك!",
  signupSuccessBody: "رح نراجع طلبك ونبعتلك إيميل التفعيل لما تتم الموافقة.",
  signupDuplicate: "في طلب بهالإيميل مستني المراجعة من قبل.",
  signupError: "ما قدرنا نبعت الطلب. جرّب مرة تانية.",
  signupHaveAccount: "عندك حساب؟ سجّل دخول",
  signupCta: "محل جديد؟ سجّل هون",
  // Navigation
  navHome: "الرئيسية",
  navOrders: "الطلبات",
  navCustomers: "الزباين",
  navInventory: "المخزون",
  navReorders: "موافقات الشرا",
  navInsights: "التوقعات",
  navChat: "المحادثة",
  navSetup: "ضبط المحل",
  // Owner chat panel
  chatTitle: "المحادثة مع مودير",
  chatPlaceholder: "اكتب سؤالك هون...",
  chatSend: "إرسال",
  chatSending: "عم نبعت...",
  chatError: "ما قدرنا نبعت الرسالة. جرّب مرة تانية.",
  chatWelcome: "أهلاً! كيف فيني ساعدك اليوم؟",
  // App shell / whoami
  shop: "المحل",
  plan: "الباقة",
  productCount: "عدد المنتجات",
  whatsappNumberLabel: "رقم واتساب المحل",
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
  // Order feed
  ordersToday: "طلبات اليوم",
  noOrdersYet: "ما في طلبات اليوم بعد",
  noOrdersHint: "أول ما يجي طلب رح يبيّن هون.",
  ordersError: "ما قدرنا نجيب الطلبات.",
  unknownCustomer: "زبون",
  fulfillmentPickup: "استلام من المحل",
  fulfillmentDelivery: "توصيل",
  statusConfirmed: "مأكّد",
  statusPreparing: "عم نحضّر",
  statusDelivered: "تسلّم",
  statusCompleted: "تخلّص",
  qtyTimes: "×", // "2 ×" before an item name
  // Order — mark complete (triggers deduction + reorder drafting)
  markComplete: "خلّص الطلب",
  markCompleteConfirmTitle: "خلّص الطلب؟",
  markCompleteConfirmBody: "رح ننقص الكميات من المخزون وما فينا نرجّع. متأكد؟",
  markCompleteConfirm: "أكّد التخليص",
  orderCompleted: "تخلّص الطلب وتنقص المخزون.",
  orderCompleteError: "ما قدرنا نخلّص الطلب. جرّب مرة تانية.",
  orderNotCompletable: "ما فينا نخلّص هالطلب (يمكن خلص قبل أو ما في مخزون كافي).",
  // Founder admin — approvals
  adminTitle: "إدارة مودير",
  adminLoginTitle: "دخول الإدارة",
  adminLoginSubtitle: "خاص بمالك مودير",
  approvalsTitle: "طلبات الاشتراك",
  approvalsEmpty: "ما في طلبات مستنية.",
  approvalsError: "ما قدرنا نجيب الطلبات.",
  reqBusiness: "اسم المحل",
  reqPhone: "رقم المالك",
  reqEmail: "الإيميل",
  reqDate: "التاريخ",
  approve: "وافق",
  reject: "ارفض",
  approveWaPrompt: "رقم واتساب المحل",
  approveConfirm: "وافق وفعّل",
  rejectReasonPrompt: "سبب الرفض",
  rejectConfirm: "ارفض الطلب",
  cancel: "إلغاء",
  statusPending: "مستني",
  statusApproved: "تمت الموافقة",
  statusRejected: "مرفوض",
  // Activation / set-password
  activateTitle: "فعّل حسابك",
  activateChecking: "عم نتأكد من الرابط...",
  activateInvalid: "رابط التفعيل مش صحيح أو خلص. اطلب رابط جديد من مودير.",
  newPassword: "كلمة السر الجديدة",
  confirmPassword: "أكّد كلمة السر",
  passwordsMismatch: "كلمتين السر مش متطابقتين.",
  passwordTooShort: "كلمة السر لازم تكون ٨ أحرف عالأقل.",
  activateButton: "فعّل وادخل",
  activateSuccess: "تفعّل حسابك! فيك تسجّل دخول هلّق.",
  goToLogin: "روح لتسجيل الدخول",
  showPassword: "إظهار",
  hidePassword: "إخفاء",
  // Customers list
  customersTitle: "الزباين",
  noCustomersYet: "ما في زباين بعد",
  noCustomersHint: "أول ما يطلب زبون رح يبيّن هون.",
  customersError: "ما قدرنا نجيب الزباين.",
  colCustomer: "الزبون",
  colOrders: "الطلبات",
  colTotalSpent: "مجموع المصاريف",
  colLastOrder: "آخر طلب",
  // Inventory list
  inventoryTitle: "المخزون",
  noInventoryYet: "ما في منتجات بمخزونك بعد",
  noInventoryHint: "ضيف منتجاتك من ضبط المحل وبعدها ضبّط الكميات هون.",
  inventoryError: "ما قدرنا نجيب المخزون.",
  colProduct: "المنتج",
  colQuantity: "الكمية",
  colThreshold: "حد إعادة الطلب",
  colReorderQty: "كمية إعادة الطلب",
  colSupplier: "المورّد",
  lowStockBadge: "مخزون منخفض",
  noSupplier: "بدون مورّد",
  notSet: "غير محدّد",
  edit: "عدّل",
  // Inventory edit form
  editInventoryTitle: "عدّل مخزون «{name}»",
  fieldQuantity: "الكمية الحالية",
  fieldThreshold: "حد إعادة الطلب",
  fieldThresholdHint: "لما تنزل الكمية لهون أو أقل منشوف إنه المخزون منخفض.",
  fieldReorderQty: "كمية إعادة الطلب",
  fieldSupplier: "المورّد",
  mustBeZeroOrMore: "لازم يكون رقم صفر أو أكتر.",
  inventorySaved: "تحفظت الكمية.",
  inventorySaveError: "ما قدرنا نحفظ. جرّب مرة تانية.",
  // ---- Owner approvals inbox (reorder POs) ----
  reordersTitle: "موافقات طلبات الشرا",
  reordersError: "ما قدرنا نجيب طلبات الشرا.",
  // Empty state copy is given verbatim in the Phase 4 spec.
  reordersEmpty: "ما في طلبات شراء بانتظار الموافقة",
  manualQueueTitle: "بدّن إرسال يدوي",
  poProduct: "المنتج",
  poQuantity: "الكمية المقترحة",
  poSupplier: "المورّد",
  poAgentNote: "ملاحظة المساعد للمورّد",
  poNoSupplier: "بدون مورّد",
  poDraftReason: "سبب الطلب",
  poDispatchError: "سبب فشل الإرسال",
  // PO status labels
  poStatusDraft: "بانتظار الموافقة",
  poStatusApproved: "تمت الموافقة",
  poStatusSent: "تبعت",
  poStatusRejected: "مرفوض",
  poStatusFailed: "فشل الإرسال",
  // Actions
  poApprove: "وافق",
  poReject: "ارفض",
  poApproveNoteLabel: "ملاحظة (اختياري)",
  poApproveConfirm: "وافق وابعت",
  poRejectReasonLabel: "سبب الرفض",
  poRejectConfirm: "ارفض الطلب",
  poMarkSent: "علّمه مبعوت",
  poRetry: "جرّب الإرسال مرة تانية",
  // Toasts
  poApproved: "تمت الموافقة — عم نبعت للمورّد.",
  poRejected: "ترفض طلب الشرا.",
  poMarkedSent: "تعلّم الطلب إنه مبعوت.",
  poActionError: "ما قدرنا نكمّل العملية. جرّب مرة تانية.",
  poRejectReasonRequired: "لازم تكتب سبب الرفض.",
  // ---- Supplier bills (OCR) ----
  navBills: "الفواتير",
  billsTitle: "فواتير الموردين",
  billsError: "ما قدرنا نجيب الفواتير.",
  billsEmpty: "ما في فواتير. صوّر فاتورة مورّد لتبلّش.",
  billUpload: "صوّر / ارفع فاتورة",
  billUploading: "عم نرفع...",
  billUploaded: "وصلت الفاتورة — عم نقراها.",
  billUploadError: "ما قدرنا نرفع الفاتورة. جرّب مرة تانية.",
  billUploadBadType: "بس صور مقبولة (JPG، PNG).",
  billSupplier: "المورّد",
  billNoSupplier: "مورّد غير معروف",
  billTotal: "المجموع",
  billDate: "التاريخ",
  billConfidence: "الثقة",
  // Bill status labels
  billStatusUploaded: "عم نرفع",
  billStatusProcessing: "عم نقرا الفاتورة",
  billStatusExtracted: "بانتظار المراجعة",
  billStatusCommitting: "عم نسجّل",
  billStatusCommitted: "تسجّلت بالمخزون",
  billStatusRejected: "مرفوضة",
  billStatusFailed: "ما قدرنا نقراها",
  // ---- Bill review screen ----
  billReviewTitle: "مراجعة الفاتورة",
  billBack: "رجوع للفواتير",
  billImageAlt: "صورة الفاتورة",
  billNoImage: "ما في صورة",
  billLines: "البنود",
  billLineName: "الاسم",
  billLineQty: "الكمية",
  billLineAmount: "القيمة",
  billLineProduct: "المنتج بالمخزون",
  billLinePick: "اختار منتج",
  billLineLowConfidence: "تأكّد من القراءة",
  billSaveLines: "احفظ التعديلات",
  billLinesSaved: "تسجّلت التعديلات.",
  billApprove: "وافق وسجّل بالمخزون",
  billApproveConfirm: "تأكيد الموافقة",
  billApproved: "تمت الموافقة — عم نسجّل بالمخزون.",
  billReject: "ارفض",
  billRejectReasonLabel: "سبب الرفض",
  billRejectConfirm: "ارفض الفاتورة",
  billRejected: "ترفضت الفاتورة.",
  billRejectReasonRequired: "لازم تكتب سبب الرفض.",
  billUnmappedWarning: "في بنود لسا ما مربوطة بمنتج. اربطهن قبل الموافقة.",
  billNotFound: "ما لقينا هالفاتورة.",
  billReviewError: "ما قدرنا نجيب الفاتورة.",
  billActionError: "ما قدرنا نكمّل العملية. جرّب مرة تانية.",
  // ---- Insights (ML predictions panel, Phase 6) ----
  insightsTitle: "توقعات الذكاء الاصطناعي",
  insightsError: "ما قدرنا نجيب التوقعات.",
  // Shown when ml_mode=stub: the models aren't serving yet, so values are blank.
  insightsStubNote: "التوقعات لسا مطفّية (النماذج قيد التدريب). عم نعرض القوائم بدون أرقام.",
  asOfLabel: "حتى",
  insightsDemandTitle: "توقّع الطلب لبكرا",
  insightsDemandEmpty: "ما في منتجات عندها تاريخ مبيعات بعد.",
  colPredictedUnits: "الكمية المتوقّعة",
  insightsChurnTitle: "زباين معرّضين للفقدان",
  insightsChurnEmpty: "ما في زباين لتقييمهم بعد.",
  colChurnRisk: "نسبة الخطر",
  insightsAnomalyTitle: "حركة المبيعات اليومية",
  insightsAnomalyEmpty: "ما في مبيعات مسجّلة بعد.",
  colDay: "اليوم",
  colRevenue: "المبيعات",
  anomalyFlag: "غير طبيعي",
  anomalyNormal: "طبيعي",
  // Pagination
  prevPage: "السابق",
  nextPage: "التالي",
  pageOf: "صفحة {n} من {total}",
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
  addingProduct: "عم نضيف...",
  noProductsYet: "ما في منتجات بعد. ضيف أول منتج.",
  atLeastOneProduct: "لازم تضيف منتج واحد على الأقل.",
  productsInStore: "منتجات محلّك",
  productAdded: "تضاف المنتج.",
  productLoadError: "ما قدرنا نجيب المنتجات.",
  // Optional initial stock seeded with a product (creates its inventory row).
  initialStock: "الكمية بالمخزون (اختياري)",
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

/** Fulfillment type → Arabic label. */
export function fulfillmentLabel(type: string): string {
  return type === "delivery" ? t.fulfillmentDelivery : t.fulfillmentPickup;
}

/**
 * Order status → Arabic label + a Tailwind class pair. Color is paired with the
 * text label (and a dot in the UI), never used alone (a11y color-not-only).
 */
export function statusMeta(status: string): { label: string; cls: string } {
  switch (status) {
    case "preparing":
      return { label: t.statusPreparing, cls: "bg-status-pending/15 text-status-pending" };
    case "delivered":
      return { label: t.statusDelivered, cls: "bg-muted text-muted-foreground" };
    case "completed":
      // Terminal state after the owner marks an order complete (stock deducted).
      return { label: t.statusCompleted, cls: "bg-muted text-muted-foreground" };
    case "confirmed":
    default:
      return { label: t.statusConfirmed, cls: "bg-accent/15 text-accent" };
  }
}

/**
 * Purchase-order status → Arabic label + a Tailwind class pair, for the owner
 * approvals inbox. Color is always paired with the text label (and a dot in the
 * UI), never used alone (a11y color-not-only).
 */
export function poStatusMeta(status: string): { label: string; cls: string } {
  switch (status) {
    case "approved":
      return { label: t.poStatusApproved, cls: "bg-accent/15 text-accent" };
    case "sent":
      return { label: t.poStatusSent, cls: "bg-muted text-muted-foreground" };
    case "rejected":
      return { label: t.poStatusRejected, cls: "bg-destructive/10 text-destructive" };
    case "dispatch_failed":
      return { label: t.poStatusFailed, cls: "bg-destructive/10 text-destructive" };
    case "draft":
    default:
      return { label: t.poStatusDraft, cls: "bg-status-pending/15 text-status-pending" };
  }
}

/**
 * Supplier-bill status → Arabic label + Tailwind class pair, for the bills list +
 * review screen. Color is always paired with the text label (a11y color-not-only).
 */
export function billStatusMeta(status: string): { label: string; cls: string } {
  switch (status) {
    case "uploaded":
      return { label: t.billStatusUploaded, cls: "bg-muted text-muted-foreground" };
    case "ocr_processing":
      return { label: t.billStatusProcessing, cls: "bg-muted text-muted-foreground" };
    case "extracted":
      return { label: t.billStatusExtracted, cls: "bg-status-pending/15 text-status-pending" };
    case "committing":
      return { label: t.billStatusCommitting, cls: "bg-accent/15 text-accent" };
    case "committed":
      return { label: t.billStatusCommitted, cls: "bg-accent/15 text-accent" };
    case "rejected":
      return { label: t.billStatusRejected, cls: "bg-destructive/10 text-destructive" };
    case "ocr_failed":
      return { label: t.billStatusFailed, cls: "bg-destructive/10 text-destructive" };
    default:
      return { label: status, cls: "bg-muted text-muted-foreground" };
  }
}
