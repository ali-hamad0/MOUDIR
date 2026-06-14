from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class OtpRequest(BaseModel):
    """Ask Modir to WhatsApp a one-time code to a phone, step 1 of signup."""

    owner_phone: str = Field(min_length=4, max_length=32)


class OtpRequestResponse(BaseModel):
    """Confirmation that a code was sent. Never echoes the code itself; returns
    the normalized E.164 number the code was sent to so the UI can display it."""

    sent_to: str


class SignupRequestCreate(BaseModel):
    """Public application to join Modir. Creates a pending request ONLY — no
    tenant, no user, no login until a founder approves. `otp_code` is the code
    delivered by the prior /signup-requests/otp call; it must match before the
    application is accepted (the phone is proven real and reachable)."""

    business_name: str = Field(min_length=1, max_length=255)
    owner_phone: str = Field(min_length=4, max_length=32)
    owner_email: EmailStr
    otp_code: str = Field(min_length=4, max_length=12)


class SignupRequestPublicResponse(BaseModel):
    """What the public applicant gets back: confirmation of receipt only. No
    internal review fields (reviewer, reason, provisioned tenant) are exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_name: str
    owner_email: str
    status: str
    created_at: datetime
