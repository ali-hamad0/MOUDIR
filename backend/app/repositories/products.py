from uuid import UUID

from sqlalchemy import func, select

from app.db.models import Product
from app.repositories.base import TenantScopedRepository


class ProductRepository(TenantScopedRepository[Product]):
    """The single product catalog repository. Base CRUD (get/list/add/delete)
    covers Phase 1 needs; later phases extend with category/availability filters.
    """

    model = Product

    async def count(self, tenant_id: UUID) -> int:
        """How many products this tenant has catalogued (drives setup_complete)."""
        stmt = select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)
        return int((await self._session.execute(stmt)).scalar_one())
