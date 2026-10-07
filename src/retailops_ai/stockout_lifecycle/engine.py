"""Typed stockout adapter; recovery and alias semantics are shared with AI 05."""

from retailops_ai.model_lifecycle.reviewed_engine import (
    Contracts,
    Journal,
    Registry,
    ReviewedLifecycle,
)
from retailops_ai.stockout_lifecycle.contract import (
    TEST_MODEL,
    StockoutBinding,
    StockoutLifecycleRequest,
    StockoutModelRelease,
    StockoutRegistrySource,
    database_release,
)


class StockoutLifecycle(
    ReviewedLifecycle[
        StockoutLifecycleRequest, StockoutRegistrySource, StockoutBinding, StockoutModelRelease
    ]
):
    def __init__(
        self,
        registry: Registry[StockoutRegistrySource, StockoutBinding],
        journal: Journal[StockoutBinding, StockoutModelRelease],
        *,
        environment: str,
    ) -> None:
        if environment not in {"local", "test"}:
            raise ValueError("stockout_lifecycle_environment")
        super().__init__(
            registry,
            journal,
            Contracts(
                request=StockoutLifecycleRequest,
                binding=StockoutBinding,
                release=StockoutModelRelease,
                database_release=database_release,
                mechanics_model=TEST_MODEL,
                error_prefix="stockout",
            ),
            environment=environment,
        )
