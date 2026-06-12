from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TenantAdminView(BaseModel):
    """One row in the founder's tenant directory."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    whatsapp_number: str
    plan_tier: str
    is_active: bool
    data_source: str | None = None
    created_at: datetime
    # Billing: raw stored fields; past_due/expired is derived from
    # current_period_end by the reader (services.billing.effective_subscription_status).
    subscription_status: str
    current_period_end: date | None = None


class TenantDetailAdminView(TenantAdminView):
    """Directory row plus tenant-scoped activity counts for the drill-down."""

    customers_count: int
    orders_today: int


class TenantActionRequest(BaseModel):
    """Suspend/reactivate always carries a reason — it lands in the audit log."""

    reason: str = Field(min_length=1, max_length=1000)


class TenantActionResponse(BaseModel):
    id: UUID
    is_active: bool


class PlanChangeRequest(BaseModel):
    """Founder changes a tenant's plan without a payment (comp/downgrade/fix)."""

    plan_tier: str = Field(min_length=1, max_length=32)


class SubscriptionOverrideRequest(BaseModel):
    """Founder sets the subscription state directly (fix / custom grant / reset).

    plan_tier="free" + current_period_end=None resets to a never-paid state.
    """

    plan_tier: str = Field(min_length=1, max_length=32)
    current_period_end: date | None = None


class PaymentRecordRequest(BaseModel):
    """Founder records an out-of-band payment (Whish/OMT/cash/card transfer)."""

    amount_usd: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    method: str = Field(min_length=1, max_length=16)
    months: int = Field(default=1, ge=1, le=24)
    note: str | None = Field(default=None, max_length=500)
    # Optional plan move in the same action (the usual "paid → pro" flow).
    plan_tier: str | None = None


class PaymentView(BaseModel):
    """One recorded payment in a tenant's billing history."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    amount_usd: Decimal
    method: str
    months: int
    note: str | None = None
    plan_tier: str
    period_end_after: date
    created_at: datetime


class PaymentRecordResponse(BaseModel):
    """The recorded payment plus the tenant's updated billing state."""

    payment: PaymentView
    plan_tier: str
    subscription_status: str
    current_period_end: date
