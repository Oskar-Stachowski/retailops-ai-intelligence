"""Read causal sales from verified Source/Curated/DQ parents without re-export or inference."""

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Literal, Protocol

from retailops_ai.agent.execution import ToolFailure
from retailops_ai.agent.tools import (
    MAX_QUALIFIED_SALES_POINTS,
    QualifiedSalesEvidence,
    SalesRequest,
    SalesResult,
    ToolInput,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.day_qualification.gate import DayGate
from retailops_ai.day_qualification.parents import parents
from retailops_ai.day_qualification.store import runtime
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.releases import IndexPin


def authorize(request: SalesRequest, principal: Principal) -> Principal:
    scope = request.scope
    if scope is None:
        raise ToolFailure("invalid_scope")
    if not (
        "operator" in principal.roles
        and {"assistant:query", "sales:read"} <= principal.capabilities
        and set(scope.product_ids) <= principal.product_ids
        and set(scope.selling_location_ids) <= principal.selling_location_ids
        and scope.channel in principal.channels
    ):
        raise ToolFailure("unauthorized")
    series = len(scope.product_ids) * len(scope.selling_location_ids)
    if (
        series > request.limit
        or series * ((request.window.end - request.window.start).days + 1)
        > MAX_QUALIFIED_SALES_POINTS
    ):
        raise ToolFailure("budget_exceeded")
    return replace(
        principal,
        product_ids=frozenset(scope.product_ids),
        selling_location_ids=frozenset(scope.selling_location_ids),
        channels=frozenset({scope.channel}),
    )


class SalesReader(Protocol):
    environment: Literal["local", "test"]

    def read(self, request: SalesRequest, principal: Principal) -> SalesResult: ...


class QualifiedSalesReader:
    """Verify parents at startup, then query a private immutable in-memory snapshot.

    The existing parent verifier reconstructs all Source → Curated → replay and
    closure assertions. Requests query knowledge at their cutoff, never the final
    daily aggregate. Restart/rebinding performs verification again.
    """

    def __init__(
        self,
        replay_dir: Path,
        coverage_dir: Path,
        curated_dir: Path,
        import_dir: Path,
        environment: Literal["local", "test"],
    ) -> None:
        if environment not in {"local", "test"}:
            raise ValueError("qualified_sales_environment_invalid")
        full, coverage, days, replay, raw, parent = parents(
            replay_dir, coverage_dir, curated_dir, import_dir
        )
        if (
            coverage.descriptor.source_dataset_id
            != full.descriptor.source_binding.source_dataset_id
        ):
            raise ValueError("qualified_sales_source_binding_mismatch")
        self.environment = environment
        self._gate = DayGate(days, replay, raw, parent)
        self._currencies: dict[tuple[str, str, str], set[str]] = {}
        for day in days:
            if day.event_type == "sale_completed":
                self._currencies.setdefault(
                    (day.product_id, day.selling_location_id, day.channel), set()
                ).add(day.currency)
        self._binding = dict(
            source_dataset_id=coverage.descriptor.source_dataset_id,
            curated_dataset_id=full.descriptor.parent.curated_dataset_id,
            full_dq_replay_id=full.full_dq_replay_id,
            full_dq_descriptor_sha256=canonical_sha256(full.descriptor.model_dump(mode="json")),
            day_coverage_id=coverage.coverage_id,
            day_coverage_descriptor_sha256=canonical_sha256(
                coverage.descriptor.model_dump(mode="json")
            ),
            qualification_runtime_sha256=canonical_sha256(runtime().model_dump(mode="json")),
        )

    @property
    def source_dataset_id(self) -> str:
        return self._binding["source_dataset_id"]

    @property
    def curated_dataset_id(self) -> str:
        return self._binding["curated_dataset_id"]

    @property
    def full_dq_replay_id(self) -> str:
        return self._binding["full_dq_replay_id"]

    @property
    def day_coverage_id(self) -> str:
        return self._binding["day_coverage_id"]

    def read(self, request: SalesRequest, principal: Principal) -> SalesResult:
        request = SalesRequest.model_validate_json(request.model_dump_json())
        authorize(request, principal)
        scope = request.scope
        if scope is None:
            raise ToolFailure("invalid_scope")
        points = []
        for product in sorted(scope.product_ids):
            for location in sorted(scope.selling_location_ids):
                currencies = self._currencies.get((product, location, scope.channel), set())
                if not currencies:
                    raise ToolFailure("not_found")
                if len(currencies) != 1:
                    raise ToolFailure("unavailable")
                currency = next(iter(currencies))
                for offset in range((request.window.end - request.window.start).days + 1):
                    day = (request.window.start + timedelta(days=offset)).isoformat()
                    points.append(
                        self._gate.point(
                            ("sale_completed", day, product, location, scope.channel, currency),
                            request.as_of.isoformat(),
                        )
                    )
        evidence = QualifiedSalesEvidence.model_validate_json(
            json.dumps(
                dict(
                    **self._binding,
                    environment=self.environment,
                    request=request.model_dump(mode="json"),
                    points=[p.model_dump(mode="json") for p in points],
                )
            )
        )
        return SalesResult(
            schema_version="1.0",
            contract_type="agent_tool_result",
            tool="get_sales_summary",
            status="ok" if evidence.complete else "no_data",
            as_of=request.as_of,
            freshness_status="current" if evidence.complete else "missing",
            source_ref=evidence.view_ref,
            items=evidence.sales_items(),
            error=None,
            source_kind="runtime",
            qualified_days=evidence,
        )


class QualifiedSalesTool:
    source_kind: Literal["runtime"] = "runtime"

    def __init__(self, reader: SalesReader, environment: Literal["local", "test"]) -> None:
        if environment not in {"local", "test"} or reader.environment != environment:
            raise ValueError("qualified_sales_environment_invalid")
        self.reader, self.environment = reader, environment

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> SalesResult:
        if not isinstance(request, SalesRequest):
            raise ToolFailure("invalid_scope")
        if self.reader.environment != self.environment:
            raise ToolFailure("unavailable")
        request = SalesRequest.model_validate_json(request.model_dump_json())
        actor = authorize(request, principal)
        try:
            output = await asyncio.to_thread(self.reader.read, request, actor)
            output = SalesResult.model_validate_json(output.model_dump_json())
            evidence = output.qualified_days
            if (
                output.source_kind != "runtime"
                or evidence is None
                or evidence.request != request
                or evidence.environment != self.environment
            ):
                raise ToolFailure("unavailable")
            return output
        except (ToolFailure, asyncio.CancelledError):
            raise
        except Exception:
            raise ToolFailure("unavailable") from None
