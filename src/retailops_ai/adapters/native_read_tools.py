"""Shared boundaries for native, read-only Assistant adapters."""

from dataclasses import replace
from typing import Literal

from retailops_ai.agent.execution import ToolFailure
from retailops_ai.agent.tools import DataRequest
from retailops_ai.domain.access import Capability, Principal


def scoped_actor(
    request: DataRequest, principal: Principal, capabilities: set[Capability]
) -> Principal:
    scope = request.scope
    if scope is None:
        raise ToolFailure("invalid_scope")
    if not (
        "operator" in principal.roles
        and {"assistant:query", *capabilities} <= principal.capabilities
        and set(scope.product_ids) <= principal.product_ids
        and set(scope.selling_location_ids) <= principal.selling_location_ids
        and scope.channel in principal.channels
    ):
        raise ToolFailure("unauthorized")
    if len(scope.product_ids) * len(scope.selling_location_ids) > request.limit:
        raise ToolFailure("budget_exceeded")
    return replace(
        principal,
        product_ids=frozenset(scope.product_ids),
        selling_location_ids=frozenset(scope.selling_location_ids),
        channels=frozenset({scope.channel}),
    )


def environment(value: str) -> Literal["local", "test"]:
    if value not in {"local", "test"}:
        raise ValueError("native_reader_environment_invalid")
    return "test" if value == "test" else "local"
