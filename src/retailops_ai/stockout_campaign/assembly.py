"""Authorized final-only assembly; no private store is opened before the exact permission."""

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from retailops_ai.curated.builder import iter_rows
from retailops_ai.curated.contract import encoded
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, inventory, read_json
from retailops_ai.stockout_campaign.contract import (
    CampaignFreeze,
    CampaignPermission,
    SourceRef,
    require_permission,
)
from retailops_ai.stockout_campaign.implementation import code_digest, lock_digest
from retailops_ai.stockout_temporal_series.bundle import (
    TemporalPreparation,
    check_partition,
    names,
    policies,
)
from retailops_ai.stockout_temporal_series.store import TemporalStore
from retailops_ai.stockout_temporal_storage.store import PartitionInputs, capture, physical_key
from retailops_ai.stockout_training.contract import MAX_BYTES, MAX_ROWS

SCENARIO_TABLES = {"promotion_plans", "fulfillment_routes", "assortment"}


@dataclass(frozen=True)
class FinalData:
    rows: list[dict[str, Any]]
    outcomes: list[int]
    coverage: dict[str, Any]
    lineage_sha256: str
    parent_seals_sha256: str
    tables: dict[str, list[dict[str, Any]]]


def guard(freeze: CampaignFreeze, permission: CampaignPermission | None, source: SourceRef) -> None:
    require_permission(freeze, permission)
    source = SourceRef.model_validate_json(source.model_dump_json())
    if source not in freeze.sources:
        raise ValueError("stockout_final_source_not_in_campaign")
    if (
        code_digest() != freeze.evaluator_code_sha256
        or lock_digest() != freeze.dependency_lock_sha256
    ):
        raise ValueError("stockout_final_execution_code_or_lock_changed")


def assemble_final(
    target: Path,
    inputs: PartitionInputs,
    *,
    freeze: CampaignFreeze,
    permission: CampaignPermission | None,
    source: SourceRef,
) -> FinalData:
    guard(freeze, permission, source)
    stored = read_json(target, "manifest.json")
    target_seal = capture(target)
    split_policy, partition_policy, storage_policy = policies(stored)
    descriptor = stored["descriptor"]
    if (
        split_policy != source.split_policy
        or stored["temporal_bundle_id"] != source.temporal_bundle_id
        or descriptor["source_dataset_id"] != source.source_dataset_id
        or descriptor["curated_dataset_id"] != source.curated_dataset_id
        or descriptor["qualification_id"] != source.qualification_id
        or descriptor["parents"]["feature_bundle_id"] != source.feature_bundle_id
        or descriptor["parents"]["upstream_bundle_id"] != source.upstream_bundle_id
        or descriptor["parents"]["label_bundle_id"] != source.label_bundle_id
    ):
        raise SnapshotError("stockout_final_frozen_parent_or_split_mismatch")
    rows, outcomes, lineage = [], [], hashlib.sha256(b"[")
    reasons: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    size = count = candidates = 0
    with TemporalStore(inputs, allow_evaluation_truth=True, policy=storage_policy) as store:
        preparation = TemporalPreparation(store, split_policy, partition_policy)
        for batch, spec, blobs in preparation.partitions():
            check_partition(target, spec, blobs)
            comparison = {physical_key(r): r for r in batch["comparison"]}
            membership = [m for m in batch["membership"] if m["role"] == "test"]
            for m in membership:
                candidates += 1
                reasons[m["reason"] or "eligible"] += 1
                status_counts[comparison[physical_key(m)]["status"]] += 1
            eligible = [m for m in membership if m["eligible"]]
            labels = {
                physical_key(p): p
                for p in store.points("labels", [physical_key(m) for m in eligible])
            }
            for m in eligible:
                k = physical_key(m)
                row, point = comparison[k], labels[k]
                origin = datetime.fromisoformat(m["as_of"])
                if (
                    row["status"] != "eligible"
                    or point["incident_stockout"] not in (0, 1)
                    or not split_policy.calibration_until <= origin < split_policy.test_until
                    or datetime.fromisoformat(point["label_available_at"])
                    > split_policy.evaluated_at
                    or datetime.fromisoformat(point["window_end_at"]) > split_policy.evaluated_at
                ):
                    raise SnapshotError("stockout_final_ineligible_or_immature_outcome")
                category, source_hash = store.category(m["product_id"], m["as_of"])
                selected = dict(
                    product_id=m["product_id"],
                    stock_location_id=m["stock_location_id"],
                    as_of=m["as_of"],
                    category_id=category,
                    values={**row["base"], **row["upstream"]},
                )
                entry = [*k, category, source_hash, point["incident_stockout"]]
                size += len(canonical_json([selected, entry]))
                count += 1
                if count > MAX_ROWS or size > MAX_BYTES:
                    raise SnapshotError("stockout_final_array_resource_limit")
                if count > 1:
                    lineage.update(b",")
                lineage.update(canonical_json(entry))
                rows.append(selected)
                outcomes.append(point["incident_stockout"])
        expected = preparation.manifest()
        inventory(target, names(expected))
        if (
            expected != stored
            or capture(target) != target_seal
            or read_json(target, "manifest.json") != stored
        ):
            raise SnapshotError("stockout_final_temporal_changed_during_replay")
        if (
            count != source.eligible_test_membership
            or len({physical_key(r) for r in rows}) != count
        ):
            raise SnapshotError("stockout_final_membership_count_or_duplicate_keys")
        curated = read_json(inputs.curated, "curated_manifest.json")
        tables: dict[str, list[dict[str, Any]]] = {}
        plan_size = plan_count = 0
        for table in curated["tables"]:
            if table["table"] not in SCENARIO_TABLES:
                continue
            selected_table = []
            for row in iter_rows(inputs.curated, table["files"], 256):
                plan_count += 1
                plan_size += len(encoded(row))
                if plan_count > MAX_ROWS or plan_size > 4 * 1024**2:
                    raise SnapshotError("stockout_final_scenario_plan_resource_limit")
                selected_table.append(row)
            tables[table["table"]] = selected_table
        if set(tables) != SCENARIO_TABLES:
            raise SnapshotError("stockout_final_scenario_plan_tables_missing")
        store.check_database()
        store.check_parents()
        from retailops_ai.data_contracts.identity import canonical_sha256

        parent_seal = canonical_sha256(store.parent_seals)
    lineage.update(b"]")
    return FinalData(
        rows,
        outcomes,
        dict(
            final_candidates=candidates,
            eligible=count,
            exclusions=dict(reasons),
            feature_statuses=dict(status_counts),
            array_bytes=size,
        ),
        lineage.hexdigest(),
        parent_seal,
        tables,
    )
