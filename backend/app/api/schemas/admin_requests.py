from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SignupRequestAdminView(BaseModel):
    """Full view of a signup request for the founder's approvals screen."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    business_name: str
    owner_phone: str
    owner_email: str
    status: str
    created_at: datetime
    reviewed_at: datetime | None = None
    reject_reason: str | None = None
    provisioned_tenant_id: UUID | None = None


class ApproveRequest(BaseModel):
    """The founder provides the shop's WhatsApp AI number (set up in Meta before
    approving). Required — a shop is never provisioned without its number."""

    whatsapp_number: str = Field(min_length=4, max_length=32)


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class AdminActionResponse(BaseModel):
    id: UUID
    status: str
    provisioned_tenant_id: UUID | None = None
