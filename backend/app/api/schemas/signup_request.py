from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignupRequestCreate(BaseModel):
    """Public application to join Modir. Creates a pending request ONLY — no
    tenant, no user, no login until a founder approves."""

    business_name: str = Field(min_length=1, max_length=255)
    owner_phone: str = Field(min_length=4, max_length=32)
    owner_email: EmailStr


class SignupRequestPublicResponse(BaseModel):
    """What the public applicant gets back: confirmation of receipt only. No
    internal review fields (reviewer, reason, provisioned tenant) are exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_name: str
    owner_email: str
    status: str
    created_at: datetime
