"""V12 adapter for the shared recoverable AI 05 lifecycle protocol."""

from retailops_ai.model_lifecycle.reviewed_engine import (
    Contracts,
    Journal,
    Registry,
    ReviewedLifecycle,
)
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    TEST_MODEL,
    V12Binding,
    V12LifecycleRequest,
    V12ModelRelease,
    V12RegistrySource,
    database_release,
)


class V12Lifecycle(
    ReviewedLifecycle[V12LifecycleRequest, V12RegistrySource, V12Binding, V12ModelRelease]
):
    def __init__(
        self,
        registry: Registry[V12RegistrySource, V12Binding],
        journal: Journal[V12Binding, V12ModelRelease],
        *,
        environment: str,
    ) -> None:
        super().__init__(
            registry,
            journal,
            Contracts(
                request=V12LifecycleRequest,
                binding=V12Binding,
                release=V12ModelRelease,
                database_release=database_release,
                mechanics_model=TEST_MODEL,
                error_prefix="v12",
            ),
            environment=environment,
        )
