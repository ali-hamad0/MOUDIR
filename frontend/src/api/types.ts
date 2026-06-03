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
