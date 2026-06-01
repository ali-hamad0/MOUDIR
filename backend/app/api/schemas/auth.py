from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Dashboard login. Email is unique per tenant, so the tenant is resolved
    from the WhatsApp number the account was registered under."""

    whatsapp_number: str = Field(min_length=4, max_length=32)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
