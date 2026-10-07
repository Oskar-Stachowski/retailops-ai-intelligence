"""Immutable verified identity and whole-scope checks; no transport or provider SDK."""

from dataclasses import dataclass
from typing import Literal

Role = Literal["viewer", "operator", "admin", "promoter", "pipeline"]
Capability = Literal[
    "forecast:read",
    "access:admin",
    "knowledge:read",
    "knowledge:index",
    "model:decide",
    "forecast:run",
    "anomaly:read",
    "anomaly:run",
    "stockout:read",
    "stockout:run",
    "assistant:query",
    "assistant:audit",
    "sales:read",
    "inventory:read",
    "anomalies:read",
    "operations:read",
    "model:read",
]
Channel = Literal["store", "online", "marketplace", "wholesale"]


# Selling scopes and physical stock scopes remain separate. In particular,
# stockout:read never grants access to a selling location by itself.
DATA_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        "assistant:query", "forecast:read", "forecast:run", "anomaly:read",
        "anomaly:run", "sales:read", "inventory:read", "anomalies:read",
        "operations:read", "model:read",
    }
)

@dataclass(frozen=True)
class KnowledgeAccess:
    environment: str
    repositories: frozenset[str]
    access_classes: frozenset[str]
    document_statuses: frozenset[str]


@dataclass(frozen=True)
class StockoutAccess:
    product_ids: frozenset[str]
    stock_location_ids: frozenset[str]


@dataclass(frozen=True)
class Principal:
    principal_id: str
    roles: frozenset[Role]
    capabilities: frozenset[Capability]
    product_ids: frozenset[str]
    selling_location_ids: frozenset[str]
    channels: frozenset[Channel]
    knowledge: KnowledgeAccess | None = None
    stockout: StockoutAccess | None = None


def can_read_forecast(
    principal: Principal, *, products: set[str], locations: set[str], channel: str
) -> bool:
    return (
        "forecast:read" in principal.capabilities
        and bool(products)
        and bool(locations)
        and products <= principal.product_ids
        and locations <= principal.selling_location_ids
        and channel in principal.channels
    )


def can_read_stockout(
    principal: Principal, *, products: set[str], stock_locations: set[str]
) -> bool:
    scope = principal.stockout
    return (
        "stockout:read" in principal.capabilities
        and scope is not None
        and bool(products)
        and bool(stock_locations)
        and products <= scope.product_ids
        and stock_locations <= scope.stock_location_ids
    )
