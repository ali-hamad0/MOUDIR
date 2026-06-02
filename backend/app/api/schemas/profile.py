from datetime import time
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---- Business profile ----
class ProfileUpsert(BaseModel):
    business_name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    location: str | None = Field(default=None, max_length=255)
    delivery_radius_km: int | None = Field(default=None, ge=0)
    accepts_delivery: bool = False
    accepts_pickup: bool = True
    logo_url: str | None = Field(default=None, max_length=1024)


class ProfileResponse(ProfileUpsert):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID


# ---- Products ----
class ProductWrite(BaseModel):
    name_ar: str = Field(min_length=1, max_length=255)
    name_en: str | None = Field(default=None, max_length=255)
    description_ar: str | None = None
    price_lbp: int | None = Field(default=None, ge=0)
    price_usd: Decimal | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=32)
    category: str | None = Field(default=None, max_length=64)
    is_available: bool = True
    image_url: str | None = Field(default=None, max_length=1024)


class ProductResponse(ProductWrite):
    model_config = ConfigDict(from_attributes=True)

    id: UUID


# ---- Operating hours (replace the whole week) ----
class DayHours(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    open_time: time | None = None
    close_time: time | None = None
    is_closed: bool = False
    note_ar: str | None = None


class OperatingHoursReplace(BaseModel):
    days: list[DayHours]


class DayHoursResponse(DayHours):
    model_config = ConfigDict(from_attributes=True)


# ---- Policies (upsert key/value) ----
class PolicyUpsert(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    value: str | None = None


class PoliciesUpsert(BaseModel):
    policies: list[PolicyUpsert]


class PolicyResponse(PolicyUpsert):
    model_config = ConfigDict(from_attributes=True)
