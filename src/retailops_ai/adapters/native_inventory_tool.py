"""Verified Source/Curated physical snapshots and causal selling-to-stock routes."""

import asyncio
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, Protocol

from retailops_ai.agent.execution import ToolFailure
from retailops_ai.agent.tools import (
    InventoryRequest,
    InventoryResult,
    NativeInventoryEvidence,
    NativeInventoryRoute,
    NativeInventorySnapshot,
    ToolInput,
)
from retailops_ai.curated.builder import build_curated, implementation, iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, cell, columns_for, encoded
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.importer import verify_import
from retailops_ai.source_snapshot.protocol import Limits

LIMITS = Limits(max_bytes=128 * 1024**2, max_rows=100000)


def authorize(request: InventoryRequest, principal: Principal) -> Principal:
    scope = request.scope
    if scope is None:
        raise ToolFailure("invalid_scope")
    physical = principal.stockout
    if not (
        "operator" in principal.roles
        and {"assistant:query", "inventory:read"} <= principal.capabilities
        and set(scope.product_ids) <= principal.product_ids
        and set(scope.selling_location_ids) <= principal.selling_location_ids
        and scope.channel in principal.channels
        and physical is not None
        and set(scope.product_ids) <= physical.product_ids
    ):
        raise ToolFailure("unauthorized")
    if len(scope.product_ids) * len(scope.selling_location_ids) > request.limit:
        raise ToolFailure("budget_exceeded")
    return replace(
        principal,
        product_ids=frozenset(scope.product_ids),
        selling_location_ids=frozenset(scope.selling_location_ids),
        channels=frozenset({scope.channel}),
        stockout=replace(physical, product_ids=frozenset(scope.product_ids)),
    )


class InventoryReader(Protocol):
    environment: Literal["local", "test"]

    def read(self, request: InventoryRequest, principal: Principal) -> InventoryResult: ...


class NativeInventoryReader:
    """Reconstruct parents at startup; retain an immutable, bounded private view.

    Unknown snapshots are retained so a newer unavailable snapshot cannot be
    replaced with an older known quantity. Neither producer imports nor queries,
    exports, models or databases are involved in ordinary requests.
    """

    def __init__(
        self, curated_dir: Path, import_dir: Path, environment: Literal["local", "test"]
    ) -> None:
        if environment not in {"local", "test"}:
            raise ValueError("native_inventory_environment_invalid")
        imported = verify_import(import_dir, limits=LIMITS)
        document = verify_curated(curated_dir, limits=LIMITS)
        if (
            document["schema_version"] not in {"1.1.0", "1.2.0"}
            or not document["readiness"]["inventory_ready"]
        ):
            raise SnapshotError("native_inventory_parent_not_ready")
        with tempfile.TemporaryDirectory(prefix="ai12-inventory-parent-") as tmp:
            scratch = Path(tmp).resolve()
            rebuilt = build_curated(import_dir, scratch / "data/generated", limits=LIMITS)
            if (
                rebuilt.manifest != document
                or document["descriptor"]["parent_source_dataset_id"] != imported.source_id
            ):
                raise SnapshotError("native_inventory_source_curated_mismatch")
            tables: dict[str, list[Any]] = {}
            count = size = 0
            for name, model in (
                ("inventory_fulfillment_routes", NativeInventoryRoute),
                ("inventory_daily_snapshots", NativeInventorySnapshot),
            ):
                spec = next(t for t in document["tables"] if t["table"] == name)
                digest = Digest(
                    scratch / (name + ".sqlite"),
                    columns_for(name, document["schema_version"]),
                    spec["grain"],
                )
                tables[name] = []
                try:
                    for row in iter_rows(curated_dir, spec["files"], LIMITS.batch_rows):
                        count += 1
                        size += len(encoded(row))
                        if count > LIMITS.max_rows or size > LIMITS.max_bytes:
                            raise SnapshotError("native_inventory_parent_limit")
                        digest.add(row)
                        # Unrequested marketplace/wholesale routes are never projected
                        # into the Assistant's store/online contract.
                        if name == "inventory_fulfillment_routes" and row["channel"] not in {
                            "store",
                            "online",
                        }:
                            continue
                        raw = {
                            k: cell(row["id" if k == "route_id" else k]) for k in model.model_fields
                        }
                        tables[name].append(model.model_validate_json(json.dumps(raw)))
                    if any(spec[k] != v for k, v in digest.summary().items()):
                        raise SnapshotError("native_inventory_changed_during_load")
                finally:
                    digest.close()
        self.environment = environment
        routes: dict[tuple[str, str], list[NativeInventoryRoute]] = {}
        snapshots: dict[tuple[str, str], list[NativeInventorySnapshot]] = {}
        for route in tables["inventory_fulfillment_routes"]:
            routes.setdefault((route.selling_location_id, route.channel), []).append(route)
        for snapshot in tables["inventory_daily_snapshots"]:
            snapshots.setdefault((snapshot.product_id, snapshot.stock_location_id), []).append(
                snapshot
            )
        self._routes = {key: tuple(value) for key, value in routes.items()}
        self._snapshots = {key: tuple(value) for key, value in snapshots.items()}
        self._binding = dict(
            source_dataset_id=imported.source_id,
            source_descriptor_sha256=canonical_sha256(imported.manifest["source"]["descriptor"]),
            curated_dataset_id=document["curated_dataset_id"],
            curated_descriptor_sha256=canonical_sha256(document["descriptor"]),
            qualification_runtime_sha256=canonical_sha256(
                implementation(document["schema_version"])
            ),
        )

    @property
    def source_dataset_id(self) -> str:
        return str(self._binding["source_dataset_id"])

    @property
    def curated_dataset_id(self) -> str:
        return str(self._binding["curated_dataset_id"])

    def read(self, request: InventoryRequest, principal: Principal) -> InventoryResult:
        request = InventoryRequest.model_validate_json(request.model_dump_json())
        actor = authorize(request, principal)
        scope, physical = request.scope, actor.stockout
        if scope is None or physical is None:
            raise ToolFailure("invalid_scope")
        points = []
        for product in sorted(scope.product_ids):
            for location in sorted(scope.selling_location_ids):
                routes = [
                    r
                    for r in self._routes.get((location, scope.channel), ())
                    if r.curated_available_at <= request.as_of
                    and r.effective_from <= request.as_of.date() < r.effective_to
                ]
                route = None
                if routes:
                    latest = max(r.version for r in routes)
                    selected = [r for r in routes if r.version == latest]
                    if len(selected) != 1:
                        raise ToolFailure("unavailable")
                    route = selected[0]
                    if route.stock_location_id not in physical.stock_location_ids:
                        raise ToolFailure("unauthorized")
                snapshot = None
                state = "missing_route"
                if route is not None:
                    state = "missing_snapshot"
                    snapshots = [
                        s
                        for s in self._snapshots.get((product, route.stock_location_id), ())
                        if s.snapshot_at <= request.as_of
                    ]
                    if snapshots:
                        latest_at = max(s.snapshot_at for s in snapshots)
                        selected_snapshots = [s for s in snapshots if s.snapshot_at == latest_at]
                        if len(selected_snapshots) != 1:
                            raise ToolFailure("unavailable")
                        selected_snapshot = selected_snapshots[0]
                        if (
                            selected_snapshot.curated_available_at is None
                            or selected_snapshot.curated_available_at <= request.as_of
                        ):
                            snapshot = selected_snapshot
                            state = (
                                "not_available"
                                if snapshot.status != "known"
                                else "stale"
                                if (request.as_of - snapshot.snapshot_at).total_seconds() > 300
                                else "known"
                            )
                points.append(
                    dict(
                        product_id=product,
                        selling_location_id=location,
                        channel=scope.channel,
                        route=route.model_dump(mode="json") if route else None,
                        snapshot=snapshot.model_dump(mode="json") if snapshot else None,
                        status=state,
                    )
                )
        evidence = NativeInventoryEvidence.model_validate_json(
            json.dumps(
                dict(
                    **self._binding,
                    environment=self.environment,
                    request=request.model_dump(mode="json"),
                    points=points,
                )
            )
        )
        return InventoryResult(
            schema_version="1.0",
            contract_type="agent_tool_result",
            tool="get_inventory_status",
            source_kind="runtime",
            status="ok" if evidence.complete else "no_data",
            as_of=evidence.as_of,
            freshness_status="current" if evidence.complete else "missing",
            source_ref=evidence.view_ref,
            items=evidence.inventory_items(),
            error=None,
            native_view=evidence,
        )


class NativeInventoryTool:
    source_kind: Literal["runtime"] = "runtime"

    def __init__(self, reader: InventoryReader, environment: Literal["local", "test"]) -> None:
        if environment not in {"local", "test"} or reader.environment != environment:
            raise ValueError("native_inventory_environment_invalid")
        self.reader, self.environment = reader, environment

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> InventoryResult:
        if not isinstance(request, InventoryRequest):
            raise ToolFailure("invalid_scope")
        if self.reader.environment != self.environment:
            raise ToolFailure("unavailable")
        request = InventoryRequest.model_validate_json(request.model_dump_json())
        actor = authorize(request, principal)
        try:
            output = await asyncio.to_thread(self.reader.read, request, actor)
            output = InventoryResult.model_validate_json(output.model_dump_json())
            evidence = output.native_view
            if (
                output.source_kind != "runtime"
                or evidence is None
                or evidence.request != request
                or evidence.environment != self.environment
            ):
                raise ToolFailure("unavailable")
            if actor.stockout is None or any(
                p.route is not None
                and p.route.stock_location_id not in actor.stockout.stock_location_ids
                for p in evidence.points
            ):
                raise ToolFailure("unauthorized")
            return output
        except (ToolFailure, asyncio.CancelledError):
            raise
        except Exception:
            raise ToolFailure("unavailable") from None
