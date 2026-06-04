from pydantic import BaseModel, EmailStr, Field


class AdminLoginRequest(BaseModel):
    """Founder/super-admin login. Email is globally unique (admins sit above
    tenants), so no whatsapp_number is needed — unlike the tenant-owner login."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AdminTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
