"""Whoami schema for the dashboard (Phase 3, Task 3.4)."""

from datetime import date
from uuid import UUID

from pydantic import BaseModel


class MeResponse(BaseModel):
    """The logged-in user's identity + a derived setup flag.

    `setup_complete` lets the frontend decide whether to launch the setup wizard
    on first login or show the "setup incomplete" banner. It is derived
    server-side (profile named + at least one product), never guessed client-side.
    """

    user_id: UUID
    email: str
    role: str
    tenant_id: UUID
    business_name: str | None = None
    # The shop's WhatsApp AI number (assigned by the founder) — shown in the
    # dashboard so the owner always knows which number their customers message.
    whatsapp_number: str
    plan_tier: str
    product_count: int
    setup_complete: bool
    # Billing (Phase 11): derived status (trialing/active/past_due/expired/
    # suspended — computed server-side), the paid-through date, and how to pay
    # (a static Whish link and/or the founder's WhatsApp — from settings; empty
    # values mean the dashboard hides that action).
    subscription_status: str
    current_period_end: date | None = None
    billing_whish_link: str = ""
    billing_contact_phone: str = ""
    # What the tenant ACTUALLY gets right now: "pro" only while the paid period
    # covers today — an expired Pro reads "free" here. The frontend uses this to
    # show locks; the backend enforces regardless (plan_gate).
    effective_plan: str
    pro_price_usd: float
    # True only when the Whish gateway is configured (mode != off): the
    # dashboard's subscribe button starts an in-app checkout. False → the
    # button links to billing_whish_link and the founder activates manually.
    online_checkout_enabled: bool
