"""Immutable verified identity and whole-scope checks; no transport or provider SDK."""

from dataclasses import dataclass
from typing import Literal

Role = Literal["viewer", "operator", "admin"]
Capability = Literal[
    "forecast:read",
    "access:admin",
    "knowledge:read",
    "knowledge:index",
    "assistant:query",
    "sales:read",
    "inventory:read",
    "stockout:read",
    "anomalies:read",
    "operations:read",
    "model:read",
]
DATA_CAPABILITIES: frozenset[Capability] = frozenset(
    {
        "forecast:read",
        "sales:read",
        "inventory:read",
        "stockout:read",
        "anomalies:read",
        "operations:read",
        "model:read",
    }
)
Channel = Literal["store", "online"]


@dataclass(frozen=True)
class KnowledgeAccess:
    environment: str
    repositories: frozenset[str]
    access_classes: frozenset[str]
    document_statuses: frozenset[str]


@dataclass(frozen=True)
class Principal:
    principal_id: str
    roles: frozenset[Role]
    capabilities: frozenset[Capability]
    product_ids: frozenset[str]
    selling_location_ids: frozenset[str]
    channels: frozenset[Channel]
    knowledge: KnowledgeAccess | None = None


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
