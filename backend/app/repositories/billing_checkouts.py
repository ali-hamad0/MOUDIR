from app.db.models import BillingCheckout
from app.repositories.base import TenantScopedRepository


class BillingCheckoutRepository(TenantScopedRepository[BillingCheckout]):
    """Whish checkout attempts. Inherits the Wall — a checkout id from another
    tenant simply misses, so no checkout can activate a foreign tenant."""

    model = BillingCheckout
