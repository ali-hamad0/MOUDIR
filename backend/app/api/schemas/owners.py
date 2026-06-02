from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AddOwnerRequest(BaseModel):
    phone_number: str = Field(min_length=4, max_length=32)
    name: str | None = Field(default=None, max_length=255)


class VerifyOwnerRequest(BaseModel):
    token: str = Field(min_length=1, max_length=64)


class OwnerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    phone_number: str
    name: str | None
    verification_status: str
    verified_at: datetime | None
