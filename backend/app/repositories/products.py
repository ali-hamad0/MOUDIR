from app.db.models import Product
from app.repositories.base import TenantScopedRepository


class ProductRepository(TenantScopedRepository[Product]):
    """The single product catalog repository. Base CRUD (get/list/add/delete)
    covers Phase 1 needs; later phases extend with category/availability filters.
    """

    model = Product
