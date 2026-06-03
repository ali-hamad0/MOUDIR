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
  plan_tier: string;
  product_count: number;
  setup_complete: boolean;
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
