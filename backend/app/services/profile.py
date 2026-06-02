import hashlib
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.profile import (
    OperatingHoursReplace,
    PoliciesUpsert,
    ProductWrite,
    ProfileUpsert,
)
from app.db.models import (
    AuditLog,
    BusinessPolicy,
    BusinessProfile,
    OperatingHours,
    Product,
)
from app.repositories.business_policies import BusinessPolicyRepository
from app.repositories.business_profile import BusinessProfileRepository
from app.repositories.knowledge_base_docs import KnowledgeBaseDocRepository
from app.repositories.operating_hours import OperatingHoursRepository
from app.repositories.products import ProductRepository
from prompts import profile_ar


def _content_hash(*parts: object) -> str:
    """Stable hash of the embeddable text. Unchanged content => unchanged hash =>
    the KB tracking row is not needlessly re-queued."""
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class ProfileService:
    """Business-profile / catalog CRUD, all tenant-scoped through repositories.
    Every product/policy/hours write updates a knowledge_base_docs tracking row
    (pending on create, stale on content change) and writes an audit_log entry.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._kb = KnowledgeBaseDocRepository(session)

    def _audit(self, tenant_id: UUID, actor_id: UUID, action: str, target: str) -> None:
        self._session.add(
            AuditLog(tenant_id=tenant_id, actor_id=actor_id, action=action, target=target)
        )

    # ---- Profile ----
    async def upsert_profile(
        self, *, tenant_id: UUID, actor_id: UUID, data: ProfileUpsert
    ) -> BusinessProfile:
        repo = BusinessProfileRepository(self._session)
        profile = await repo.get_for_tenant(tenant_id)
        if profile is None:
            profile = await repo.add(tenant_id, BusinessProfile())
        for field, value in data.model_dump().items():
            setattr(profile, field, value)
        await self._session.flush()
        self._audit(tenant_id, actor_id, "profile.updated", "business_profile")
        await self._session.commit()
        return profile

    # ---- Products ----
    async def create_product(
        self, *, tenant_id: UUID, actor_id: UUID, data: ProductWrite
    ) -> Product:
        product = await ProductRepository(self._session).add(
            tenant_id, Product(**data.model_dump())
        )
        await self._track_product(tenant_id, product)
        self._audit(tenant_id, actor_id, "product.created", str(product.id))
        await self._session.commit()
        return product

    async def update_product(
        self, *, tenant_id: UUID, actor_id: UUID, product_id: UUID, data: ProductWrite
    ) -> Product:
        repo = ProductRepository(self._session)
        product = await repo.get(tenant_id, product_id)
        if product is None:
            # A cross-tenant id simply misses the scoped lookup -> 404, not 403.
            raise HTTPException(status.HTTP_404_NOT_FOUND, profile_ar.PRODUCT_NOT_FOUND)
        for field, value in data.model_dump().items():
            setattr(product, field, value)
        await self._session.flush()
        await self._track_product(tenant_id, product)
        self._audit(tenant_id, actor_id, "product.updated", str(product.id))
        await self._session.commit()
        return product

    async def delete_product(self, *, tenant_id: UUID, actor_id: UUID, product_id: UUID) -> None:
        deleted = await ProductRepository(self._session).delete(tenant_id, product_id)
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, profile_ar.PRODUCT_NOT_FOUND)
        self._audit(tenant_id, actor_id, "product.deleted", str(product_id))
        await self._session.commit()

    async def _track_product(self, tenant_id: UUID, product: Product) -> None:
        await self._kb.mark_pending_or_stale(
            tenant_id,
            "product",
            product.id,
            _content_hash(
                product.name_ar,
                product.name_en,
                product.description_ar,
                product.price_lbp,
                product.price_usd,
                product.unit,
                product.category,
                product.is_available,
            ),
        )

    # ---- Operating hours (replace whole week) ----
    async def replace_hours(
        self, *, tenant_id: UUID, actor_id: UUID, data: OperatingHoursReplace
    ) -> list[OperatingHours]:
        repo = OperatingHoursRepository(self._session)
        result: list[OperatingHours] = []
        for day in data.days:
            row = await repo.get_by_day(tenant_id, day.day_of_week)
            if row is None:
                row = await repo.add(tenant_id, OperatingHours(day_of_week=day.day_of_week))
            row.open_time = day.open_time
            row.close_time = day.close_time
            row.is_closed = day.is_closed
            row.note_ar = day.note_ar
            await self._session.flush()
            await self._kb.mark_pending_or_stale(
                tenant_id,
                "operating_hours",
                row.id,
                _content_hash(
                    row.day_of_week, row.open_time, row.close_time, row.is_closed, row.note_ar
                ),
            )
            result.append(row)
        self._audit(tenant_id, actor_id, "hours.updated", "operating_hours")
        await self._session.commit()
        return result

    # ---- Policies (upsert key/value) ----
    async def upsert_policies(
        self, *, tenant_id: UUID, actor_id: UUID, data: PoliciesUpsert
    ) -> list[BusinessPolicy]:
        repo = BusinessPolicyRepository(self._session)
        result: list[BusinessPolicy] = []
        for item in data.policies:
            policy = await repo.get_by_key(tenant_id, item.key)
            if policy is None:
                policy = await repo.add(tenant_id, BusinessPolicy(key=item.key))
            policy.value = item.value
            await self._session.flush()
            await self._kb.mark_pending_or_stale(
                tenant_id,
                "policy",
                policy.id,
                _content_hash(policy.key, policy.value),
            )
            result.append(policy)
        self._audit(tenant_id, actor_id, "policies.updated", "business_policies")
        await self._session.commit()
        return result
