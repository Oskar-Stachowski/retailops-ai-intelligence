"""Server-owned identity, finite tool budget, and fail-closed adapter output checks."""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, Protocol

from retailops_ai.agent.native_forecast import NativeForecastRead
from retailops_ai.agent.tools import (
    INPUT,
    OUTPUT,
    AnomalyItem,
    AnomalyRequest,
    DataRequest,
    DataScope,
    ForecastResult,
    InventoryItem,
    KnowledgeRequest,
    KnowledgeResult,
    ModelStatusItem,
    RiskItem,
    RiskRequest,
    SalesItem,
    SalesRequest,
    ToolInput,
    ToolName,
    ToolOutput,
    ToolPolicy,
)
from retailops_ai.data_contracts.common import SellingKey
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.tool import ERRORS, ToolError, ToolRequest
from retailops_ai.domain.access import Capability, Principal
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.pipelines.retrieval import KnowledgeDenied, allowed, context_size, resolve_scope
from retailops_ai.security.local import LocalAccess, strict_json

READ_CAPABILITIES: dict[str, Capability] = {
    "get_sales_summary": "sales:read",
    "get_inventory_status": "inventory:read",
    "get_demand_forecast": "forecast:read",
    "get_stockout_risk": "stockout:read",
    "get_detected_anomalies": "anomalies:read",
    "get_live_operations": "operations:read",
    "get_model_status": "model:read",
    "search_knowledge": "knowledge:read",
}


class ToolFailure(ValueError):
    """Canonical public error, never the upstream exception or rejected input."""

    def __init__(self, code: str) -> None:
        self.code = code
        description, retryable = ERRORS[code]
        self.error = ToolError.model_validate(
            {"code": code, "description": description, "retryable": retryable}
        )
        super().__init__(description)


class ToolAdapter(Protocol):
    source_kind: Literal["fixture", "runtime"]

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> ToolOutput: ...


@dataclass(frozen=True)
class ToolAudit:
    tool: ToolName
    status: Literal["ok", "no_data", "error"]
    duration_ms: float
    error_code: str | None
    source_refs: tuple[str, ...]


class ToolExecutor:
    def __init__(
        self,
        authority: LocalAccess,
        adapters: Mapping[str, ToolAdapter],
        policy: ToolPolicy,
        environment: Literal["local", "test"],
        *,
        allow_fixtures: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timer: Callable[[], float] = monotonic,
    ) -> None:
        if not set(adapters) <= set(READ_CAPABILITIES):
            raise ValueError("adapter_outside_read_only_catalogue")
        if allow_fixtures and environment != "test":
            raise ValueError("fixture_tools_require_test_environment")
        self.authority = authority
        self.adapters = dict(adapters)
        self.policy = ToolPolicy.model_validate_json(policy.model_dump_json())
        self.environment = environment
        self.allow_fixtures = allow_fixtures
        self.clock = clock
        self.timer = timer

    def open_session(
        self,
        authorization: str | None,
        *,
        default_scope: DataScope | None = None,
        pin: IndexPin | None = None,
    ) -> "ToolSession":
        principal = self.authority.authenticate(authorization, now=self.clock())
        if (
            principal is None
            or "operator" not in principal.roles
            or "assistant:query" not in principal.capabilities
        ):
            raise ToolFailure("unauthorized")
        if default_scope is not None:
            default_scope = DataScope.model_validate_json(default_scope.model_dump_json())
            check_scope(principal, default_scope, self.policy)
        if pin is not None:
            pin = IndexPin.model_validate_json(pin.model_dump_json())
            if pin.environment != self.environment or pin.lane != "retrieval":
                raise ToolFailure("unavailable")
        return ToolSession(self, principal, default_scope, pin)


def check_scope(principal: Principal, scope: DataScope, policy: ToolPolicy) -> None:
    if (
        len(scope.product_ids) > policy.max_products
        or len(scope.selling_location_ids) > policy.max_locations
    ):
        raise ToolFailure("invalid_scope")
    if not (
        set(scope.product_ids) <= principal.product_ids
        and set(scope.selling_location_ids) <= principal.selling_location_ids
        and scope.channel in principal.channels
    ):
        raise ToolFailure("unauthorized")


class ToolSession:
    def __init__(
        self,
        executor: ToolExecutor,
        principal: Principal,
        default_scope: DataScope | None,
        pin: IndexPin | None,
    ) -> None:
        self.executor = executor
        self.principal = principal
        self.default_scope = default_scope
        self.pin = pin
        self.deadline = executor.timer() + executor.policy.request_deadline_seconds
        self.calls = 0
        self.audit: list[ToolAudit] = []
        self._accepted_outputs: dict[str, str] = {}
        self._accepted_calls: dict[str, tuple[str, str]] = {}
        self._chat_claimed = False
        self._budget_lock = asyncio.Lock()

    def claim_chat_session(self) -> None:
        if self._chat_claimed:
            raise ValueError("chat_session_already_bound")
        self._chat_claimed = True

    def validate_call(self, raw: str) -> ToolInput:
        """Validate a proposed model call without executing it or consuming tool budget."""
        if len(raw.encode()) > 16384:
            raise ToolFailure("invalid_scope")
        try:
            strict_json(raw.encode())
            request = INPUT.validate_json(raw)
        except (ValueError, RecursionError):
            raise ToolFailure("invalid_scope") from None
        try:
            request = self._authorize(request)
        except KnowledgeDenied:
            self.audit.append(ToolAudit(request.tool, "error", 0.0, "unauthorized", ()))
            raise ToolFailure("unauthorized") from None
        except ToolFailure as exc:
            self.audit.append(ToolAudit(request.tool, "error", 0.0, exc.code, ()))
            raise
        return request

    async def execute_json(self, raw: str) -> ToolOutput:
        request = self.validate_call(raw)
        async with self._budget_lock:
            remaining = self.deadline - self.executor.timer()
            if self.calls >= self.executor.policy.max_calls or remaining <= 0:
                raise ToolFailure("budget_exceeded")
            self.calls += 1
        start = self.executor.timer()
        try:
            adapter = self.executor.adapters.get(request.tool)
            if adapter is None or (
                adapter.source_kind == "fixture" and not self.executor.allow_fixtures
            ):
                raise ToolFailure("unavailable")
            output = await asyncio.wait_for(
                adapter.execute(
                    INPUT.validate_json(request.model_dump_json()), self.principal, self.pin
                ),
                timeout=min(remaining, self.executor.policy.tool_timeout_seconds),
            )
            if self.executor.timer() >= self.deadline:
                raise ToolFailure("budget_exceeded")
            raw_output = output.model_dump_json()
            if len(raw_output.encode()) > self.executor.policy.max_result_bytes:
                raise ToolFailure("unavailable")
            output = OUTPUT.validate_json(raw_output)
            if output.source_kind != adapter.source_kind or output.tool != request.tool:
                raise ToolFailure("unavailable")
            self._check_output(request, output)
            if self.executor.timer() >= self.deadline:
                raise ToolFailure("budget_exceeded")
            status: Literal["ok", "no_data", "error"]
            refs: tuple[str, ...]
            if isinstance(output, ForecastResult):
                status = output.result.status
                refs = (output.result.source_ref,) if output.result.source_ref else ()
            elif isinstance(output, KnowledgeResult):
                status, refs = output.status, (output.index_id,)
            else:
                status = output.status
                refs = (output.source_ref,) if output.source_ref else ()
            self.audit.append(ToolAudit(request.tool, status, self._elapsed(start), None, refs))
            self._accepted_outputs[canonical_sha256(output.model_dump(mode="json"))] = (
                output.model_dump_json()
            )
            self._accepted_calls[canonical_sha256(request.model_dump(mode="json"))] = (
                request.model_dump_json(),
                output.model_dump_json(),
            )
            return output
        except asyncio.CancelledError:
            self.audit.append(
                ToolAudit(request.tool, "error", self._elapsed(start), "cancelled", ())
            )
            raise
        except TimeoutError:
            failure = ToolFailure("budget_exceeded")
        except ToolFailure as exc:
            failure = exc
        except Exception:
            failure = ToolFailure("unavailable")
        self.audit.append(ToolAudit(request.tool, "error", self._elapsed(start), failure.code, ()))
        raise failure from None

    def _elapsed(self, start: float) -> float:
        return max(0.0, (self.executor.timer() - start) * 1000)

    def accepted_outputs(self) -> tuple[ToolOutput, ...]:
        """Detached snapshots of results that passed this principal's output checks."""
        return tuple(OUTPUT.validate_json(value) for value in self._accepted_outputs.values())

    def accepted_calls(self) -> tuple[tuple[ToolInput, ToolOutput], ...]:
        return tuple(
            (INPUT.validate_json(request), OUTPUT.validate_json(output))
            for request, output in self._accepted_calls.values()
        )

    def _authorize(self, request: ToolInput) -> ToolInput:
        policy = self.executor.policy
        if READ_CAPABILITIES[request.tool] not in self.principal.capabilities:
            raise ToolFailure("unauthorized")
        if isinstance(request, KnowledgeRequest):
            resolve_scope(self.principal, request.retrieval, self.executor.environment)
            if self.pin is None:
                raise ToolFailure("unavailable")
            return request
        if isinstance(request, DataRequest):
            if request.scope is None:
                if self.default_scope is None:
                    raise ToolFailure("invalid_scope")
                request = INPUT.validate_json(
                    request.model_copy(update={"scope": self.default_scope}).model_dump_json()
                )
            if not isinstance(request, DataRequest) or request.scope is None:
                raise ToolFailure("invalid_scope")
            scope = request.scope
        else:
            scope = DataScope(
                product_ids=request.scope.product_ids,
                selling_location_ids=request.scope.selling_location_ids,
                channel=request.scope.channel,
            )
        check_scope(self.principal, scope, policy)
        if request.as_of > self.executor.clock() or request.limit > policy.max_rows:
            raise ToolFailure("invalid_scope")
        if (
            isinstance(request, (SalesRequest, AnomalyRequest, RiskRequest))
            and (request.window.end - request.window.start).days + 1 > policy.max_period_days
        ):
            raise ToolFailure("invalid_scope")
        if (
            isinstance(request, ToolRequest)
            and (request.scope.target_to - request.scope.target_from).days + 1
            > policy.max_period_days
        ):
            raise ToolFailure("invalid_scope")
        return request

    def _check_output(self, request: ToolInput, output: ToolOutput) -> None:
        if isinstance(request, KnowledgeRequest):
            if not isinstance(output, KnowledgeResult) or self.pin is None:
                raise ToolFailure("unavailable")
            pin = self.pin
            knowledge_scope = resolve_scope(
                self.principal, request.retrieval, self.executor.environment
            )
            chunk_ids = {entry.chunk_id for entry in pin.manifest.entries}
            size = context_size(output.items)
            kinds = {
                "specified": "plan",
                "implemented": "implementation",
                "verified": "verified_evidence",
                "historical": "historical_reference",
                "deprecated": "historical_reference",
            }
            if (
                output.index_id != pin.manifest.index_id
                or output.pin_generation != pin.generation
                or len(output.items) > request.retrieval.top_k
                or len({hit.chunk.chunk_id for hit in output.items}) != len(output.items)
                or output.context_bytes != size
                or output.context_tokens != (size + 3) // 4
                or output.context_tokens > request.retrieval.max_context_tokens
                or any(
                    hit.chunk.chunk_id not in chunk_ids
                    or not allowed(hit.chunk, knowledge_scope, frozenset())
                    or hit.claim_kind != kinds[hit.chunk.document_status]
                    for hit in output.items
                )
            ):
                raise ToolFailure("unavailable")
            return
        if isinstance(output, ForecastResult):
            if not isinstance(request, ToolRequest) or output.result.request != request:
                raise ToolFailure("unavailable")
            if isinstance(output.result, NativeForecastRead):
                result = output.result
                now = self.executor.clock()
                if (
                    output.source_kind != "runtime"
                    or result.environment != self.executor.environment
                    or result.page.generated_at > now
                    or (now - result.page.generated_at).total_seconds()
                    > self.executor.policy.freshness_seconds
                ):
                    raise ToolFailure("unavailable")
                if any(
                    row.approval_valid_until <= now
                    or (now - row.forecast_origin).total_seconds()
                    > result.page.freshness_policy.max_origin_age_seconds
                    for row in result.items
                ):
                    raise ToolFailure("stale")
            if output.result.error is not None:
                raise ToolFailure(output.result.error.code)
            if output.result.freshness_status in {"stale", "unknown"}:
                raise ToolFailure("stale")
            return
        if isinstance(output, KnowledgeResult):
            raise ToolFailure("unavailable")
        if output.error is not None:
            raise ToolFailure(output.error.code)
        if output.as_of is None or output.as_of > request.as_of:
            raise ToolFailure("unavailable")
        stale = (
            request.as_of - output.as_of
        ).total_seconds() > self.executor.policy.freshness_seconds
        if stale:
            raise ToolFailure("stale")
        if output.freshness_status == "stale":
            raise ToolFailure("stale")
        if len(output.items) > request.limit:
            raise ToolFailure("unavailable")
        scope = request.scope
        if scope is None:
            raise ToolFailure("invalid_scope")
        seen: set[tuple[str, str, str, str | None]] = set()
        for item in output.items:
            if not isinstance(item, SellingKey) or not (
                item.product_id in scope.product_ids
                and item.selling_location_id in scope.selling_location_ids
                and item.channel == scope.channel
            ):
                raise ToolFailure("unavailable")
            physical = item.stock_location_id if isinstance(item, InventoryItem) else None
            key = (item.product_id, item.selling_location_id, item.channel, physical)
            if key in seen:
                raise ToolFailure("unavailable")
            seen.add(key)
            if isinstance(request, (SalesRequest, AnomalyRequest, RiskRequest)) and (
                not isinstance(item, (SalesItem, AnomalyItem, RiskItem))
                or item.window != request.window
            ):
                raise ToolFailure("unavailable")
            if isinstance(item, RiskItem) and (
                item.inventory_as_of > request.as_of
                or (request.as_of - item.inventory_as_of).total_seconds()
                > self.executor.policy.freshness_seconds
            ):
                raise ToolFailure("stale")
            if (
                isinstance(item, ModelStatusItem)
                and item.deployment_environment != self.executor.environment
            ):
                raise ToolFailure("unavailable")
