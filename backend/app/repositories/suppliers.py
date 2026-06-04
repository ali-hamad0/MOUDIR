from app.db.models import Supplier
from app.repositories.base import TenantScopedRepository


class SupplierRepository(TenantScopedRepository[Supplier]):
    """Tenant-scoped supplier repository. Base CRUD (get/list/add/delete) is all
    Phase 4 needs — `list(tenant_id)` already returns this tenant's suppliers
    through `_require_tenant_scope`, so no extra methods are added here. A PO
    references a supplier (Task 4.7); both stay on the same side of the Wall."""

    model = Supplier
