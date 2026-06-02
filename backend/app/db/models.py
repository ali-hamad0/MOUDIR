from datetime import datetime, time
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
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


class BusinessProfile(Base):
    """The shop's public identity. One row per tenant (tenant_id is the key)."""

    __tablename__ = "business_profile"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_profile_tenant"),)

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delivery_radius_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accepts_delivery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    accepts_pickup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    logo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class Product(Base):
    """THE product catalog. Reused by Phase 2 (orders), 4 (inventory),
    5 (OCR mapping), 6 (ML forecasting). One table — never a per-phase copy."""

    __tablename__ = "products"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name_ar: Mapped[str] = mapped_column(String(255), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description_ar: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_lbp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_usd: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class OperatingHours(Base):
    """When the shop is open, per day of week (0=Mon ... 6=Sun)."""

    __tablename__ = "operating_hours"
    __table_args__ = (UniqueConstraint("tenant_id", "day_of_week", name="uq_hours_tenant_day"),)

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    open_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    close_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Lebanese Arabic exception note, e.g. "مغلق خلال رمضان بعد الإفطار".
    note_ar: Mapped[str | None] = mapped_column(Text, nullable=True)


class BusinessPolicy(Base):
    """Key/value store for shop rules: min_order_lbp, delivery_fee_lbp, etc."""

    __tablename__ = "business_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_policy_tenant_key"),)

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgeBaseDoc(Base):
    """Tracks what is embedded in pgvector and whether it is in sync.

    source_type: "product" | "policy" | "faq" | "operating_hours"
    embedding_status: "pending" | "embedded" | "stale"
    Phase 5 processes these; Phase 1 only creates/updates the tracking rows.
    """

    __tablename__ = "knowledge_base_docs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_type", "source_id", name="uq_kb_tenant_source"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")


# ── Phase 2 — Customer Order Flow ───────────────────────────────────────────


class Order(Base):
    """A confirmed customer order, scoped to a tenant + customer.

    Phase 2 writes status='confirmed'. No inventory deduction here (Phase 4),
    no ML (Phase 6). Totals are snapshots taken at confirm time.
    """

    __tablename__ = "orders"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="confirmed")
    fulfillment_type: Mapped[str] = mapped_column(String(16), nullable=False, default="pickup")
    # The customer's raw Arabic time phrase ("بكرا الصبح"); a parsed timestamp is
    # best-effort and may be null in Phase 2.
    requested_time_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_lbp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_usd: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    raw_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderItem(Base):
    """One line of an order.

    References a real products.id for THIS tenant; the product name and unit
    price are snapshotted so a later catalog edit can never rewrite a past order
    (the catalog is mutable; the order is a record).
    """

    __tablename__ = "order_items"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, index=True
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True
    )
    name_ar_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_lbp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_price_usd: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    line_total_lbp: Mapped[int | None] = mapped_column(Integer, nullable=True)


class OrderEvent(Base):
    """Lightweight per-order trail (created, rail_tripped, parse_retry, ...).

    The cross-cutting audit_log still records tenant-level events via
    AuditService; this table keeps order-specific breadcrumbs close to the order.
    """

    __tablename__ = "order_events"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    order_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True, index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
