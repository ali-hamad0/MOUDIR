// API response/request shapes mirrored from the backend Pydantic schemas.

export interface LoginRequest {
  whatsapp_number: string;
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
}

// GET /me — mirrors app/api/schemas/me.py::MeResponse
export interface Me {
  user_id: string;
  email: string;
  role: string;
  tenant_id: string;
  business_name: string | null;
  whatsapp_number: string;
  plan_tier: string;
  product_count: number;
  setup_complete: boolean;
}

// ---- Orders feed (mirrors app/api/schemas/orders.py) ----

export interface OrderItemRead {
  id: string;
  product_id: string;
  name_ar_snapshot: string;
  quantity: number;
  unit_price_lbp: number | null;
  unit_price_usd: string | null;
  line_total_lbp: number | null;
}

export interface OrderRead {
  id: string;
  status: string; // "confirmed" in Phase 2
  fulfillment_type: string; // "pickup" | "delivery"
  requested_time_text: string | null;
  total_lbp: number | null;
  total_usd: string | null; // Decimal serialized as string
  created_at: string;
  customer_id: string;
  customer_display_name: string | null;
  items: OrderItemRead[];
}

export interface OrdersPage {
  items: OrderRead[];
  total: number;
  limit: number;
  offset: number;
}

// ---- Inventory + suppliers (mirrors app/api/schemas/inventory.py) ----

export interface InventoryRead {
  product_id: string;
  name_ar: string;
  name_en: string | null;
  quantity: number;
  reorder_threshold: number | null;
  reorder_quantity: number | null;
  supplier_id: string | null;
  is_low: boolean;
}

export interface InventoryPage {
  items: InventoryRead[];
  total: number;
  limit: number;
  offset: number;
}

// PUT /inventory/{product_id}  (InventoryUpsert). Optional fields are null when
// the owner clears them; the backend treats null threshold as "untracked low".
export interface InventoryUpsert {
  quantity: number;
  reorder_threshold: number | null;
  reorder_quantity: number | null;
  supplier_id: string | null;
}

export interface SupplierRead {
  id: string;
  name: string;
  dispatch_type: string;
  webhook_url: string | null;
  contact_email: string | null;
  is_active: boolean;
}

// ---- Approvals inbox / reorder POs (mirrors app/api/schemas/approvals.py) ----

// One purchase order in the owner's HIL inbox. status walks
// draft → approved → sent, or draft → rejected, or → dispatch_failed (manual
// queue). The inbox lists draft + dispatch_failed; approve/reject act on drafts,
// mark-sent closes a dispatch_failed PO out of band.
export interface ApprovalRead {
  id: string;
  status: string;
  quantity: number;
  product_id: string;
  product_name_ar: string;
  product_name_en: string | null;
  supplier_id: string | null;
  supplier_name: string | null;
  draft_reason: string | null;
  agent_note_ar: string | null;
  reject_reason: string | null;
  dispatch_error: string | null;
  dispatch_attempts: number;
  reviewed_at: string | null;
  dispatched_at: string | null;
  created_at: string;
}

export interface ApprovalsPage {
  items: ApprovalRead[];
  total: number;
  limit: number;
  offset: number;
}

// ---- Supplier bills (OCR) — mirrors app/api/schemas/bills.py ----

// Numeric fields are serialized as strings (backend Decimal → JSON string).
export interface BillRead {
  id: string;
  status: string;
  supplier_id: string | null;
  supplier_name: string | null;
  original_filename: string | null;
  bill_date: string | null;
  total_amount: string | null;
  currency: string | null;
  min_confidence: string | null;
  reject_reason: string | null;
  reviewed_at: string | null;
  committed_at: string | null;
  created_at: string;
}

export interface BillsPage {
  items: BillRead[];
  total: number;
  limit: number;
  offset: number;
}

export interface BillUploadAccepted {
  id: string;
  status: string;
}

export interface BillLineRead {
  id: string;
  raw_text: string | null;
  name_ar: string | null;
  quantity: string | null;
  unit: string | null;
  unit_amount: string | null;
  line_amount: string | null;
  confidence: string | null;
  product_id: string | null;
  product_name_ar: string | null;
  committed: boolean;
}

export interface BillDetail extends BillRead {
  image_url: string | null;
  lines: BillLineRead[];
}

export interface BillLineUpdate {
  id: string;
  name_ar?: string | null;
  quantity?: string | null;
  unit?: string | null;
  unit_amount?: string | null;
  line_amount?: string | null;
  product_id?: string | null;
}

// ---- Customers list (mirrors app/api/schemas/customers.py) ----

export interface CustomerRead {
  id: string;
  display_name: string | null;
  phone_number: string;
  first_seen_at: string;
  order_count: number;
  total_spent_lbp: number;
  total_spent_usd: string | null;
  last_order_at: string | null;
}

export interface CustomersPage {
  items: CustomerRead[];
  total: number;
  limit: number;
  offset: number;
}

// ---- Profile / catalog (mirrors app/api/schemas/profile.py) ----

// PUT /profile  (ProfileUpsert)
export interface ProfileUpsert {
  business_name?: string | null;
  description?: string | null;
  location?: string | null;
  delivery_radius_km?: number | null;
  accepts_delivery: boolean;
  accepts_pickup: boolean;
  logo_url?: string | null;
}

// POST /products  (ProductWrite). Prices: price_usd is a string to keep the
// backend Decimal exact (numbers lose precision); the backend coerces it.
export interface ProductWrite {
  name_ar: string;
  name_en?: string | null;
  description_ar?: string | null;
  price_lbp?: number | null;
  price_usd?: string | null;
  unit?: string | null;
  category?: string | null;
  is_available: boolean;
  image_url?: string | null;
}

export interface ProductResponse extends ProductWrite {
  id: string;
}

// PUT /operating-hours  (OperatingHoursReplace). Times are "HH:MM" or null.
export interface DayHours {
  day_of_week: number; // 0=Mon ... 6=Sun
  open_time?: string | null;
  close_time?: string | null;
  is_closed: boolean;
  note_ar?: string | null;
}

// PUT /policies  (PoliciesUpsert)
export interface PolicyUpsert {
  key: string;
  value?: string | null;
}

// ---- ML predictions (mirrors app/api/schemas/predictions.py) ----
// `mode` is "trained" | "stub" (honesty marker); a null value means "no signal".

export interface DemandPrediction {
  product_id: string;
  predicted_units: number | null;
}

export interface DemandPredictions {
  as_of: string;
  mode: string;
  items: DemandPrediction[];
}

export interface ChurnPrediction {
  customer_id: string;
  risk: number | null; // probability in [0, 1]
}

export interface ChurnPredictions {
  as_of: string;
  mode: string;
  items: ChurnPrediction[];
}

export interface AnomalyDay {
  day: string;
  revenue_lbp: number;
  is_anomalous: boolean | null;
}

export interface AnomalyPredictions {
  as_of: string;
  mode: string;
  items: AnomalyDay[];
}
