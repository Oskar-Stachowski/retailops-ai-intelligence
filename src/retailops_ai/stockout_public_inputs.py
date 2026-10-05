"""Replay sealed public parents before registering bounded physical inference inputs."""

import hashlib
from importlib.resources import files
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from retailops_ai.data_contracts.common import UtcTime
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, read_json
from retailops_ai.stockout.feature_contract import FeaturePolicy
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_history.bundle import HistoryPreparation
from retailops_ai.stockout_preparation.bundle import PartitionPolicy, _check_partition
from retailops_ai.stockout_runtime.inputs import InputPoint, PhysicalScope, PreparedStockoutInputs
from retailops_ai.stockout_runtime.inputs import prepare_inputs as prepare_20
from retailops_ai.stockout_storage.store import DiskFacts, StoragePolicy
from retailops_ai.stockout_temporal_storage.store import capture
from retailops_ai.stockout_upstream_series.bundle import (
    UpstreamPreparation,
    check_feature_parent,
    check_partition,
    names,
    policies,
)
from retailops_ai.stockout_upstream_series.store import SeriesFacts


def implementation() -> str:
    return hashlib.sha256(
        files("retailops_ai").joinpath("stockout_public_inputs.py").read_bytes()
    ).hexdigest()


def prepare_21(
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
    with SeriesFacts(curated, policy=storage_policy) as upstream_facts:
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


def prepare_inputs(
    curated: Path, features: Path, upstream: Path, *, scope: PhysicalScope, as_of: UtcTime
) -> PreparedStockoutInputs:
    """Route sealed 2.0/2.1 public bundles without changing frozen scoring code."""
    scope = PhysicalScope.model_validate_json(scope.model_dump_json())
    as_of = TypeAdapter(UtcTime).validate_python(as_of)
    version = read_json(upstream, "manifest.json")["descriptor"]["schema_version"]
    if version == "2.0.0":
        return prepare_20(curated, features, upstream, scope=scope, as_of=as_of)
    if version == "2.1.0":
        return prepare_21(curated, features, upstream, scope=scope, as_of=as_of)
    raise SnapshotError("stockout_inference_unsupported_upstream_version")
