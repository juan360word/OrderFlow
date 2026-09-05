"""Single import point for every ORM model.

Alembic's autogenerate compares ``Base.metadata`` against the live database.
A model class only registers itself in that metadata when its module is
imported, so a table nobody imported is invisible to autogenerate — and the
migration silently drops it. Importing everything here, and importing this
module from ``migrations/env.py``, removes that whole class of bug.
"""

from orderflow.db.base import Base
from orderflow.modules.auth.models import RefreshToken, Role, User
from orderflow.modules.inventory.models import Inventory, InventoryReservation
from orderflow.modules.orders.models import Order, OrderItem
from orderflow.modules.products.models import Product

__all__ = [
    "Base",
    "Inventory",
    "InventoryReservation",
    "Order",
    "OrderItem",
    "Product",
    "RefreshToken",
    "Role",
    "User",
]
