from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.api.schemas.admin import AdminLoginRequest, AdminTokenResponse
from app.api.schemas.admin_requests import (
    AdminActionResponse,
    ApproveRequest,
    RejectRequest,
    SignupRequestAdminView,
)
from app.api.schemas.admin_tenants import (
    PaymentRecordRequest,
    PaymentRecordResponse,
    PaymentView,
    PlanChangeRequest,
    SubscriptionOverrideRequest,
    TenantActionRequest,
    TenantActionResponse,
    TenantAdminView,
    TenantDetailAdminView,
)
from app.db.models import Admin
from app.db.session import get_db_session
from app.infra.email import EmailSender
from app.infra.logging import get_logger
from app.infra.security import create_admin_token, verify_password
from app.infra.settings import Settings, get_settings
from app.repositories.admins import AdminRepository
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.business_policies import BusinessPolicyRepository
from app.repositories.customers import CustomerRepository
from app.repositories.orders import OrderRepository
from app.repositories.signup_requests import SignupRequestRepository
from app.repositories.subscription_payments import SubscriptionPaymentRepository
from app.repositories.tenants import TenantRepository
from app.services.billing import override_subscription, record_payment, set_plan
from app.services.cost_dashboard import build_daily_costs
from app.services.onboarding import approve_request, reject_request
from app.services.tenant_admin import set_tenant_active

router = APIRouter(prefix="/admin", tags=["admin"])
log = get_logger(__name__)

# Founder-only dependencies for the admin surface below.
CurrentAdmin = Annotated[Admin, Depends(get_current_admin)]
Db = Annotated[AsyncSession, Depends(get_db_session)]
Cfg = Annotated[Settings, Depends(get_settings)]


@router.post("/login", response_model=AdminTokenResponse)
async def admin_login(
    payload: AdminLoginRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminTokenResponse:
    """Founder/super-admin login. Issues an "admin"-type token that a tenant-user
    dependency rejects. Same vague error for every failure mode — never leak
    whether the email or the password was wrong.
    """
    admin = await AdminRepository(db).get_by_email(payload.email)
    if (
        admin is None
        or not admin.is_active
        or not verify_password(payload.password, admin.hashed_password)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    # Above-tenant event: there is no tenant_id to attach to audit_log (which is
    # tenant-scoped), so we record it in the structured log. Tenant-scoped admin
    # actions (approve/reject) are audited against the provisioned tenant later.
    log.info("admin.login", admin_id=str(admin.id))

    token = create_admin_token(settings, admin_id=admin.id)
    return AdminTokenResponse(access_token=token, expires_in_minutes=settings.jwt_expiry_minutes)


@router.get("/signup-requests", response_model=list[SignupRequestAdminView])
async def list_signup_requests(
    _admin: CurrentAdmin,
    db: Db,
    status_filter: str | None = None,
) -> list[SignupRequestAdminView]:
    """Founder-only: list signup requests, optionally filtered by status."""
    rows = await SignupRequestRepository(db).list_by_status(status_filter)
    return [SignupRequestAdminView.model_validate(r) for r in rows]


@router.post("/signup-requests/{request_id}/approve", response_model=AdminActionResponse)
async def approve_signup_request(
    request_id: UUID,
    payload: ApproveRequest,
    admin: CurrentAdmin,
    db: Db,
    settings: Cfg,
) -> AdminActionResponse:
    """Founder-only: approve a request → provision the tenant (via register_tenant),
    issue a one-time activation token, and email the link."""
    req = await approve_request(
        db,
        settings=settings,
        mailer=EmailSender(settings),
        admin=admin,
        request_id=request_id,
        whatsapp_number=payload.whatsapp_number,
    )
    return AdminActionResponse(
        id=req.id, status=req.status, provisioned_tenant_id=req.provisioned_tenant_id
    )


@router.post("/signup-requests/{request_id}/reject", response_model=AdminActionResponse)
async def reject_signup_request(
    request_id: UUID,
    payload: RejectRequest,
    admin: CurrentAdmin,
    db: Db,
) -> AdminActionResponse:
    """Founder-only: reject a request with a reason. Provisions nothing."""
    req = await reject_request(db, admin=admin, request_id=request_id, reason=payload.reason)
    return AdminActionResponse(id=req.id, status=req.status)


@router.get("/costs")
async def get_agent_costs(
    _admin: CurrentAdmin,
    db: Db,
    tenant_id: UUID,
    from_date: date | None = None,
    to_date: date | None = None,
) -> dict[str, dict[str, float]]:
    """Founder-only: return per-day, per-agent LLM cost estimates for a tenant.

    Response shape: {"YYYY-MM-DD": {"supervisor": 0.001234, "finance": 0.002, ...}}

    The Wall: tenant_id is required — the endpoint cannot return all-tenant data.
    This is a read-only aggregation endpoint; no writes. Requires admin token.
    """
    summary = await AgentRunRepository(db).daily_summary(
        tenant_id, from_date=from_date, to_date=to_date
    )
    log.info(
        "admin.costs.queried",
        tenant_id=str(tenant_id),
        from_date=str(from_date),
        to_date=str(to_date),
        days_returned=len(summary),
    )
    return {str(d): agents for d, agents in summary.items()}


@router.get("/costs/summary")
async def get_costs_summary(
    _admin: CurrentAdmin,
    db: Db,
    tenant_id: UUID,
) -> dict:
    """Founder-only: today's LLM spend vs budget for a tenant.

    Returns {today_usd, budget_usd, percentage, alert_triggered}.
    budget_usd = 0 means no limit (alert_triggered is always False).
    """
    today = date.today()
    summary = await AgentRunRepository(db).daily_summary(tenant_id, from_date=today, to_date=today)
    today_usd = sum(sum(agents.values()) for agents in summary.values())

    budget_policy = await BusinessPolicyRepository(db).get_by_key(tenant_id, "daily_llm_budget_usd")
    budget_usd = float(budget_policy.value or 0) if budget_policy else 0.0

    if budget_usd > 0:
        percentage = round((today_usd / budget_usd) * 100, 2)
        alert_triggered = today_usd >= budget_usd
    else:
        percentage = 0.0
        alert_triggered = False

    return {
        "today_usd": round(today_usd, 6),
        "budget_usd": budget_usd,
        "percentage": percentage,
        "alert_triggered": alert_triggered,
    }


# ── Tenant directory (founder-only) ──────────────────────────────────────────
# The one deliberate above-tenant read surface: a platform founder listing the
# businesses on their own platform. Reachable only behind get_current_admin,
# and every access is logged (the brief's audit rule for cross-business reads).


@router.get("/tenants", response_model=list[TenantAdminView])
async def list_tenants(_admin: CurrentAdmin, db: Db) -> list[TenantAdminView]:
    """Founder-only: the tenant directory — every business on the platform."""
    rows = await TenantRepository(db).list_all()
    log.info("admin.tenants.listed", count=len(rows))
    return [TenantAdminView.model_validate(r) for r in rows]


@router.get("/tenants/{tenant_id}", response_model=TenantDetailAdminView)
async def get_tenant_detail(tenant_id: UUID, _admin: CurrentAdmin, db: Db) -> TenantDetailAdminView:
    """Founder-only: one tenant with activity counts. Counts are tenant-scoped
    repository reads — the Wall holds even on the founder surface."""
    tenant = await TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")

    customers_count = await CustomerRepository(db).count(tenant_id)
    orders_today = await OrderRepository(db).count_today(tenant_id)
    log.info("admin.tenant.viewed", tenant_id=str(tenant_id))
    return TenantDetailAdminView(
        **TenantAdminView.model_validate(tenant).model_dump(),
        customers_count=customers_count,
        orders_today=orders_today,
    )


@router.get("/tenants/{tenant_id}/costs")
async def get_tenant_costs_dashboard(tenant_id: UUID, _admin: CurrentAdmin, db: Db) -> dict:
    """Founder-only: 30-day daily cost data for one tenant, in the exact shape
    of the owner's GET /dashboard/costs so the frontend reuses the same chart."""
    tenant = await TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    log.info("admin.tenant.costs.viewed", tenant_id=str(tenant_id))
    return await build_daily_costs(db, tenant_id)


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantActionResponse)
async def suspend_tenant(
    tenant_id: UUID, payload: TenantActionRequest, admin: CurrentAdmin, db: Db
) -> TenantActionResponse:
    """Founder-only: suspend a tenant. Cuts dashboard login AND WhatsApp routing
    immediately (is_active is checked at every tenant load). Audited with reason."""
    tenant = await set_tenant_active(
        db, admin=admin, tenant_id=tenant_id, active=False, reason=payload.reason
    )
    return TenantActionResponse(id=tenant.id, is_active=tenant.is_active)


@router.post("/tenants/{tenant_id}/reactivate", response_model=TenantActionResponse)
async def reactivate_tenant(
    tenant_id: UUID, payload: TenantActionRequest, admin: CurrentAdmin, db: Db
) -> TenantActionResponse:
    """Founder-only: reactivate a suspended tenant. Audited with reason."""
    tenant = await set_tenant_active(
        db, admin=admin, tenant_id=tenant_id, active=True, reason=payload.reason
    )
    return TenantActionResponse(id=tenant.id, is_active=tenant.is_active)


# ── Billing (founder-only, manual payments) ──────────────────────────────────


@router.post("/tenants/{tenant_id}/plan", response_model=TenantAdminView)
async def change_tenant_plan(
    tenant_id: UUID, payload: PlanChangeRequest, admin: CurrentAdmin, db: Db
) -> TenantAdminView:
    """Founder-only: change a tenant's plan tier without a payment. Audited."""
    tenant = await set_plan(db, admin=admin, tenant_id=tenant_id, plan_tier=payload.plan_tier)
    return TenantAdminView.model_validate(tenant)


@router.put("/tenants/{tenant_id}/subscription", response_model=TenantAdminView)
async def override_tenant_subscription(
    tenant_id: UUID, payload: SubscriptionOverrideRequest, admin: CurrentAdmin, db: Db
) -> TenantAdminView:
    """Founder-only: set the subscription state directly — fix a wrong entry,
    grant a custom period, or reset (free + no period). Audited before → after."""
    tenant = await override_subscription(
        db,
        admin=admin,
        tenant_id=tenant_id,
        plan_tier=payload.plan_tier,
        current_period_end=payload.current_period_end,
    )
    return TenantAdminView.model_validate(tenant)


@router.post("/tenants/{tenant_id}/payments", response_model=PaymentRecordResponse)
async def record_tenant_payment(
    tenant_id: UUID, payload: PaymentRecordRequest, admin: CurrentAdmin, db: Db
) -> PaymentRecordResponse:
    """Founder-only: record an out-of-band payment (Whish/OMT/cash/card) and
    extend the tenant's paid period. Audited with amount, method and new end."""
    tenant, payment = await record_payment(
        db,
        admin=admin,
        tenant_id=tenant_id,
        amount_usd=payload.amount_usd,
        method=payload.method,
        months=payload.months,
        note=payload.note,
        plan_tier=payload.plan_tier,
    )
    return PaymentRecordResponse(
        payment=PaymentView.model_validate(payment),
        plan_tier=tenant.plan_tier,
        subscription_status=tenant.subscription_status,
        current_period_end=tenant.current_period_end,  # set by record_payment
    )


@router.get("/tenants/{tenant_id}/payments", response_model=list[PaymentView])
async def list_tenant_payments(tenant_id: UUID, admin: CurrentAdmin, db: Db) -> list[PaymentView]:
    """Founder-only: a tenant's payment history, most recent first."""
    tenant = await TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    rows = await SubscriptionPaymentRepository(db).list_for_tenant(tenant_id)
    log.info("admin.tenant.payments.listed", tenant_id=str(tenant_id), count=len(rows))
    return [PaymentView.model_validate(r) for r in rows]
