"""Replay sealed public parents before registering bounded physical inference inputs."""

import hashlib
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, TypeAdapter, field_validator, model_validator

from retailops_ai.data_contracts.common import Contract, Symbol, UtcTime
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, read_json
from retailops_ai.stockout.feature_contract import FeaturePoint, FeaturePolicy
from retailops_ai.stockout.upstream_contract import UpstreamPoint
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_history.bundle import HistoryPreparation
from retailops_ai.stockout_preparation.bundle import PartitionPolicy, _check_partition
from retailops_ai.stockout_runtime.contracts import RuntimeLineage
from retailops_ai.stockout_storage.store import DiskFacts, StoragePolicy
from retailops_ai.stockout_temporal_storage.store import capture
from retailops_ai.stockout_upstream_storage.bundle import (
    UpstreamPreparation,
    check_feature_parent,
    check_partition,
    names,
    policies,
)
from retailops_ai.stockout_upstream_storage.store import UpstreamFacts

MAX_INPUT_BYTES = 16 * 1024**2


class PhysicalScope(Contract):
    product_ids: tuple[Symbol, ...] = Field(min_length=1, max_length=20)
    stock_location_ids: tuple[Symbol, ...] = Field(min_length=1, max_length=5)

    @field_validator("product_ids", "stock_location_ids", mode="before")
    @classmethod
    def wire_arrays(cls, value: object) -> object:
        # FastAPI decodes JSON before validation; retain strict member types.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def unique(self) -> Self:
        if any(
            len(values) != len(set(values))
            for values in (self.product_ids, self.stock_location_ids)
        ):
            raise ValueError("stockout_inference_duplicate_scope")
        return self


class InputPoint(Contract):
    feature: FeaturePoint
    category_id: Symbol | None
    category_available_at: UtcTime | None
    upstream: UpstreamPoint

    @model_validator(mode="after")
    def same_origin(self) -> Self:
        f, u = self.feature, self.upstream
        if (
            (f.product_id, f.stock_location_id, f.as_of)
            != (u.product_id, u.stock_location_id, u.as_of)
            or (self.category_id is None) != (self.category_available_at is None)
            or (self.category_available_at is not None and self.category_available_at > f.as_of)
            or (f.status == "eligible" and self.category_id is None)
        ):
            raise ValueError("stockout_inference_physical_or_PIT_catalog_mismatch")
        return self


class PreparedStockoutInputs(Contract):
    version: Literal["stockout-prepared-inputs-1.0.0"] = "stockout-prepared-inputs-1.0.0"
    inputs_id: Annotated[str, Field(pattern=r"^stockout-inputs-sha256-[0-9a-f]{64}$")]
    input_role: Literal["inference_public_facts_only"] = "inference_public_facts_only"
    scope: PhysicalScope
    as_of: UtcTime
    points: tuple[InputPoint, ...] = Field(min_length=1, max_length=100)
    lineage: RuntimeLineage
    source_parent_files_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    preparation_code_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_freshness_evidence: Literal["curated_does_not_supply_a_global_source_watermark"] = (
        "curated_does_not_supply_a_global_source_watermark"
    )
    parent_replay: Literal["complete_public_features_and_upstream"] = (
        "complete_public_features_and_upstream"
    )

    @model_validator(mode="after")
    def identity_and_coverage(self) -> Self:
        expected = {(p, s) for p in self.scope.product_ids for s in self.scope.stock_location_ids}
        actual = [(r.feature.product_id, r.feature.stock_location_id) for r in self.points]
        if (
            len(actual) != len(set(actual))
            or set(actual) != expected
            or actual != sorted(actual)
            or any(r.feature.as_of != self.as_of for r in self.points)
            or self.lineage.source_watermark is not None
            or self.lineage.source_completeness_status != "unavailable"
            or self.inputs_id
            != "stockout-inputs-sha256-"
            + digest(self.model_dump(mode="json", exclude={"inputs_id"}))
            or len(canonical_json(self.model_dump(mode="json"))) > MAX_INPUT_BYTES
        ):
            raise ValueError("stockout_inference_identity_scope_or_resource_limit")
        return self


def implementation() -> str:
    return hashlib.sha256(
        files("retailops_ai.stockout_runtime").joinpath("inputs.py").read_bytes()
    ).hexdigest()


def prepare_inputs(
    curated: Path,
    features: Path,
    upstream: Path,
    *,
    scope: PhysicalScope,
    as_of: UtcTime,
) -> PreparedStockoutInputs:
    """No private root, labels, arbitrary caller feature matrix or implicit cache."""
    scope = PhysicalScope.model_validate_json(scope.model_dump_json())
    as_of = TypeAdapter(UtcTime).validate_python(as_of)
    roots = dict(curated=curated, features=features, upstream=upstream)
    before = {n: capture(p) for n, p in roots.items()}
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    expected_keys = {(p, s) for p in scope.product_ids for s in scope.stock_location_ids}
    stored = read_json(features, "manifest.json")
    descriptor = stored["descriptor"]
    if (
        descriptor["schema_version"] != "2.2.0"
        or descriptor["role"] != "stockout_feature_partitions"
    ):
        raise SnapshotError("stockout_inference_feature_parent_version")
    origins = []
    with DiskFacts(
        curated, policy=StoragePolicy.model_validate(descriptor["storage"]["policy"])
    ) as facts:
        preparation = HistoryPreparation(
            facts,
            FeaturePolicy.model_validate(descriptor["feature_policy"]),
            PartitionPolicy.model_validate(descriptor["partition_policy"]),
        )
        for points, spec, blobs in preparation.partitions():
            _check_partition(features, spec, blobs)
            for point in points:
                origins.append(
                    (
                        point.product_id,
                        point.stock_location_id,
                        point.as_of,
                        point.status == "eligible",
                    )
                )
                key = point.product_id, point.stock_location_id
                if point.as_of == as_of and key in expected_keys:
                    if key in selected:
                        raise SnapshotError("stockout_inference_duplicate_physical_origin")
                    catalog = facts.known(*key, as_of)["product_catalog"]
                    if len(catalog) > 1:
                        raise SnapshotError("stockout_inference_ambiguous_PIT_category")
                    selected[key] = dict(
                        feature=point.model_dump(mode="json"),
                        category_id=catalog[0]["category_id"] if catalog else None,
                        category_available_at=catalog[0]["curated_available_at"].isoformat()
                        if catalog
                        else None,
                    )
        parent = preparation.manifest()
        if stored != parent:
            raise SnapshotError("stockout_inference_feature_parent_full_replay")
    check_feature_parent(features, parent)
    if set(selected) != expected_keys:
        raise SnapshotError("stockout_inference_requested_origin_not_covered")
    stored_upstream = read_json(upstream, "manifest.json")
    upstream_policy, partition_policy, storage_policy = policies(stored_upstream)
    origins.sort(key=lambda row: (row[2], row[0], row[1]))
    with UpstreamFacts(curated, policy=storage_policy) as upstream_facts:
        forecast = UpstreamPreparation(
            upstream_facts, parent, origins, upstream_policy, partition_policy
        )
        for forecast_points, spec, blob in forecast.partitions():
            check_partition(upstream, spec, blob)
            for upstream_point in forecast_points:
                key = upstream_point.product_id, upstream_point.stock_location_id
                if upstream_point.as_of == as_of and key in selected:
                    if "upstream" in selected[key]:
                        raise SnapshotError("stockout_inference_duplicate_upstream_origin")
                    selected[key]["upstream"] = upstream_point.model_dump(mode="json")
        expected_upstream = forecast.manifest()
        from retailops_ai.source_snapshot.files import inventory

        inventory(upstream, names(expected_upstream))
        if expected_upstream != stored_upstream:
            raise SnapshotError("stockout_inference_upstream_full_replay")
    if before != {n: capture(p) for n, p in roots.items()}:
        raise SnapshotError("stockout_inference_public_parent_changed")
    d = parent["descriptor"]
    body = dict(
        version="stockout-prepared-inputs-1.0.0",
        input_role="inference_public_facts_only",
        scope=scope.model_dump(mode="json"),
        as_of=TypeAdapter(UtcTime).dump_python(as_of, mode="json"),
        points=[
            InputPoint.model_validate_json(canonical_json(selected[k])).model_dump(mode="json")
            for k in sorted(selected)
        ],
        lineage=dict(
            source_dataset_id=d["source_dataset_id"],
            curated_dataset_id=d["curated_dataset_id"],
            feature_set_id=parent["feature_bundle_id"],
            upstream_bundle_id=expected_upstream["upstream_bundle_id"],
            source_watermark=None,
            source_completeness_status="unavailable",
        ),
        source_parent_files_sha256=digest(before),
        preparation_code_sha256=implementation(),
        source_freshness_evidence="curated_does_not_supply_a_global_source_watermark",
        parent_replay="complete_public_features_and_upstream",
    )
    return PreparedStockoutInputs.model_validate_json(
        canonical_json(dict(inputs_id="stockout-inputs-sha256-" + digest(body), **body))
    )
