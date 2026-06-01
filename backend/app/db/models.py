from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base ORM class. Every model inherits from this."""

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Tenant(Base):
    """A business on the platform. The root of every tenant-scoped query."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The business's WhatsApp Business number — the 'to' on every inbound message.
    # Unique across the platform: two tenants cannot share a destination number.
    whatsapp_number: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    plan_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TenantOwner(Base):
    """A phone number authorized to talk to Modir as the owner over WhatsApp.

    Distinct from `users` (dashboard logins). A phone here gets owner-level agent
    access — adding one is a privilege escalation and MUST go through verification.
    """

    __tablename__ = "tenant_owners"
    __table_args__ = (
        # Same phone can own different tenants, but only once per tenant.
        UniqueConstraint("tenant_id", "phone_number", name="uq_owner_tenant_phone"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "pending" | "verified" — an owner phone is only trusted once verified.
    verification_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")


class User(Base):
    """Dashboard account (email + password). Logs into the React app via JWT.

    Related to TenantOwner but separate — an accountant may have a dashboard
    login with no WhatsApp owner authority.
    """

    __tablename__ = "users"
    __table_args__ = (
        # Email is unique within a tenant, not globally — two tenants may both
        # have an "owner@shop.com".
        UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="owner")


class Customer(Base):
    """Auto-created on first WhatsApp message from an unknown number.

    Scoped to a tenant: the same phone messaging two tenants is two customers.
    """

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_number", name="uq_customer_tenant_phone"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    """Every auth event, privilege change, and owner-phone/product change."""

    __tablename__ = "audit_log"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    # Who performed the action (a user id or tenant_owner id). Nullable for
    # signup, where the actor does not yet exist.
    actor_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
