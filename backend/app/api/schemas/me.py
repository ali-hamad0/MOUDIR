"""Whoami schema for the dashboard (Phase 3, Task 3.4)."""

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
    plan_tier: str
    product_count: int
    setup_complete: bool
