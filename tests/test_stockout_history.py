"""Prefix ledger parity, causal day buckets, native roundtrip and isolated format identity."""

import random
from copy import deepcopy
from datetime import timedelta

import pytest
from test_stockout_features import OPENING, ORIGIN, row
from test_stockout_features import native as native
from test_stockout_features import records as records
from test_stockout_partitions import decoded_partition, reseal

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.stockout.feature_contract import FeaturePoint, FeaturePolicy
from retailops_ai.stockout.feature_dataset import feature_implementation, load_features_input
from retailops_ai.stockout.features import feature_point as original_point
from retailops_ai.stockout.features import inventory_day, observed_day
from retailops_ai.stockout_history import bundle
from retailops_ai.stockout_history.bundle import build_history_bundle, verify_history_bundle
from retailops_ai.stockout_history.index import HistoryIndex
from retailops_ai.stockout_history.projection import feature_point
from retailops_ai.stockout_preparation.bundle import implementation as partition_implementation
from retailops_ai.stockout_preparation.index import FactIndex
from retailops_ai.stockout_storage.bundle import build_disk_bundle, verify_disk_bundle
from retailops_ai.stockout_storage.bundle import implementation as storage_implementation
from retailops_ai.stockout_storage.store import StoragePolicy


def projected(records, origin=ORIGIN, **kw):
    return feature_point(records, product="product", stock="stock", as_of=origin, **kw)


def original(records, origin=ORIGIN, **kw):
    return original_point(records, product="product", stock="stock", as_of=origin, **kw)


def test_seeded_ledgers_keep_all_days_balances_and_instantaneous_zeroes(records):
    randomizer = random.Random(9182)  # noqa: S311 - deterministic synthetic ledger fixture
    for attempt in range(80):
        balance = attempt % 7
        ledger = [{**records["inventory_ledger"][0], "quantity_delta": balance, "sequence": 0}]
        for sequence in range(1, 101):
            delta = randomizer.randint(-balance, 9)
            balance += delta
            ledger.append(
                row(
                    occurred_at=OPENING + timedelta(hours=sequence * 3),
                    sequence=sequence,
                    quantity_delta=delta,
                    movement_type="inventory_adjustment",
                )
            )
        randomizer.shuffle(ledger)
        known = {**records, "inventory_ledger": ledger}
        index = HistoryIndex(known)
        for offset in range(-1, 30):
            day = OPENING.date() + timedelta(days=offset)
            assert index.inventory_day(day) == inventory_day(known, day)


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "first_not_opening",
        "opening_midday",
        "opening_zero",
        "no_coverage",
        "coverage_starts_late",
        "coverage_end_before_midnight",
        "coverage_end_at_midnight",
        "multiple_coverage_rows",
        "extra_opening_same_midnight",
        "negative_in_day",
        "negative_without_coverage",
    ],
)
def test_opening_coverage_and_error_boundaries_keep_v1_semantics(records, case):
    day = OPENING.date()
    records["inventory_history_coverage"] = [
        row(covered_from_at=OPENING, covered_through_at=OPENING + timedelta(days=2))
    ]
    if case == "empty":
        records["inventory_ledger"] = []
    elif case == "first_not_opening":
        records["inventory_ledger"][0]["movement_type"] = "inventory_adjustment"
    elif case == "opening_midday":
        records["inventory_ledger"][0]["occurred_at"] += timedelta(hours=1)
    elif case == "opening_zero":
        records["inventory_ledger"][0]["quantity_delta"] = 0
    elif case in {"no_coverage", "negative_without_coverage"}:
        records["inventory_history_coverage"] = []
    elif case == "coverage_starts_late":
        records["inventory_history_coverage"][0]["covered_from_at"] += timedelta(microseconds=1)
    elif case == "coverage_end_before_midnight":
        records["inventory_history_coverage"][0]["covered_through_at"] = OPENING + timedelta(
            days=1, microseconds=-1
        )
    elif case == "coverage_end_at_midnight":
        records["inventory_history_coverage"][0]["covered_through_at"] = OPENING + timedelta(days=1)
    elif case == "multiple_coverage_rows":
        records["inventory_history_coverage"].extend(
            [
                row(covered_from_at=OPENING, covered_through_at=OPENING + timedelta(days=1)),
                row(
                    covered_from_at=OPENING + timedelta(microseconds=1),
                    covered_through_at=OPENING + timedelta(days=500),
                ),
            ]
        )
    elif case == "extra_opening_same_midnight":
        records["inventory_ledger"].append(
            {**records["inventory_ledger"][0], "sequence": 1, "quantity_delta": 8}
        )
    if case.startswith("negative"):
        records["inventory_ledger"].append(
            row(
                occurred_at=OPENING + timedelta(hours=1),
                sequence=999,
                quantity_delta=-1000,
                movement_type="sale",
            )
        )
    index = HistoryIndex(records)
    if case == "negative_in_day":
        with pytest.raises(ValueError, match="invalid_known_stockout_ledger_balance"):
            inventory_day(records, day)
        with pytest.raises(ValueError, match="invalid_known_stockout_ledger_balance"):
            index.inventory_day(day)
    else:
        assert index.inventory_day(day) == inventory_day(records, day)


def test_coincident_zero_and_restore_preserve_sequence_and_onsets(records):
    records["inventory_ledger"] = [records["inventory_ledger"][0]]
    stamp = OPENING + timedelta(hours=1)
    for sequence, delta in [(1, -156), (2, 156), (3, -156), (4, 156)]:
        records["inventory_ledger"].append(
            row(
                occurred_at=stamp,
                sequence=sequence,
                quantity_delta=delta,
                movement_type="inventory_adjustment",
            )
        )
    records["inventory_history_coverage"] = [
        row(covered_from_at=OPENING, covered_through_at=OPENING + timedelta(days=1))
    ]
    records["inventory_ledger"].reverse()
    assert (
        HistoryIndex(records).inventory_day(OPENING.date())
        == inventory_day(records, OPENING.date())
        == (True, False, 2)
    )


@pytest.mark.parametrize(
    "status,units",
    [("observed_positive", 17), ("observed_zero", 0), ("closed", 0), ("missing", None)],
)
def test_daily_bucket_preserves_revision_coverage_and_zero_semantics(records, status, units):
    day = records["daily_demand_versions"][0]["business_date"]
    records["daily_demand_versions"].append(
        {
            **records["daily_demand_versions"][0],
            "version": 2,
            "observation_status": status,
            "observed_units": units,
        }
    )
    index = HistoryIndex(records)
    assert (
        index.observed_day("product", "stock", day)
        == observed_day(records, "product", "stock", day)
        == units
    )
    assert index.observed_day("product", "stock", day - timedelta(days=100)) is None


def test_daily_bucket_retains_ambiguity_and_physical_mapping_checks(records):
    day = records["daily_demand_versions"][0]["business_date"]
    records["daily_demand_versions"][0]["mapped_stock_location_id"] = "other"
    assert HistoryIndex(records).observed_day("product", "stock", day) is None
    records["daily_demand_versions"].append(dict(records["daily_demand_versions"][0]))
    with pytest.raises(ValueError, match="ambiguous"):
        HistoryIndex(records).observed_day("product", "stock", day)


@pytest.mark.parametrize(
    "gate",
    ["curated_available_at", "occurred_at", "snapshot_at", "ordered_at", "received_at", "known_at"],
)
def test_full_projection_clips_every_future_timestamp_before_history_index(records, gate):
    incoming = row(
        product_id="product",
        stock_location_id="stock",
        replenishment_order_id="order",
        ordered_quantity=5,
        expected_delivery_at=ORIGIN + timedelta(days=2),
        ordered_at=ORIGIN,
    )
    incoming[gate] = ORIGIN + timedelta(microseconds=1)
    records["replenishment_orders"].append(incoming)
    for offset in (0, 1):
        origin = ORIGIN + timedelta(microseconds=offset)
        assert projected(records, origin) == original(records, origin)


@pytest.mark.parametrize("within_window", [True, False])
def test_late_sales_and_coverage_revisions_rebuild_past_days_only_after_knowledge_boundary(
    records, within_window
):
    old = projected(records)
    cutoff = ORIGIN + timedelta(seconds=1)
    records["daily_demand_versions"].append(
        row(
            available=cutoff,
            **{
                k: v
                for k, v in records["daily_demand_versions"][-3 if within_window else 0].items()
                if k
                not in {"curated_available_at", "source_record_sha256", "version", "observed_units"}
            },
            version=2,
            observed_units=99,
        )
    )
    records["inventory_history_coverage"].append(
        row(
            available=cutoff,
            product_id="product",
            stock_location_id="stock",
            covered_from_at=OPENING,
            covered_through_at=cutoff + timedelta(days=1),
        )
    )
    for origin in (cutoff, ORIGIN, cutoff, ORIGIN):
        assert projected(records, origin) == original(records, origin)
    assert projected(records) == old
    if within_window:
        assert (
            projected(records, cutoff).values.observed_sales_mean > old.values.observed_sales_mean
        )
    else:
        assert (
            projected(records, cutoff).values.observed_sales_mean == old.values.observed_sales_mean
        )
    assert projected(records, cutoff).lineage != old.lineage


@pytest.mark.parametrize(
    "policy", [FeaturePolicy(minimum_known_days=28), FeaturePolicy(max_snapshot_age_seconds=0)]
)
def test_nondefault_feature_policy_is_unchanged(records, policy):
    assert projected(records, policy=policy) == original(records, policy=policy)


def test_full_native_points_in_reverse_keep_all_values_history_lineage_and_status(native):
    _, curated, features, _ = native
    _, records = load_features_input(curated.directory)
    index = FactIndex(records)
    for body in reversed(features["points"]):
        point = FeaturePoint.model_validate_json(canonical_json(body))
        actual = feature_point(
            index.known(point.product_id, point.stock_location_id, point.as_of),
            product=point.product_id,
            stock=point.stock_location_id,
            as_of=point.as_of,
        )
        assert actual == point


def test_native_bundle_parquet_roundtrip_and_old_identities_are_preserved(native, tmp_path):
    _, curated, features, _ = native
    old = feature_implementation(), partition_implementation(), storage_implementation()
    prior, target = tmp_path / "prior", tmp_path / "history"
    previous, _ = build_disk_bundle(curated.directory, prior)
    document, status = build_history_bundle(curated.directory, target)
    assert status == "published" and document["descriptor"]["schema_version"] == "2.2.0"
    assert document["descriptor"]["partitions"] == previous["descriptor"]["partitions"]
    assert document["feature_bundle_id"] != previous["feature_bundle_id"]
    assert [
        p for spec in document["descriptor"]["partitions"] for p in decoded_partition(target, spec)
    ] == features["points"]
    assert verify_history_bundle(target, curated.directory) == document
    assert build_history_bundle(curated.directory, target) == (document, "reused")
    assert verify_disk_bundle(prior, curated.directory) == previous
    assert old == (feature_implementation(), partition_implementation(), storage_implementation())
    assert not list(tmp_path.glob(".stockout-*"))
    with pytest.raises(SnapshotError, match="version_required"):
        verify_history_bundle(prior, curated.directory)


@pytest.mark.parametrize("mutation", ["file", "history_seal", "duplicate", "missing", "extra"])
def test_full_replay_refuses_resealed_or_incomplete_history_bundle(native, tmp_path, mutation):
    _, curated, _, _ = native
    target = tmp_path / "history"
    document, _ = build_history_bundle(curated.directory, target)
    file = document["descriptor"]["partitions"][-1]["files"][-1]
    if mutation == "file":
        (target / file["path"]).write_bytes(b"changed")
    elif mutation == "history_seal":
        document["descriptor"]["history_index"]["scope"] = "global_latest"
    elif mutation == "duplicate":
        document["descriptor"]["partitions"].append(
            deepcopy(document["descriptor"]["partitions"][0])
        )
    elif mutation == "missing":
        (target / file["path"]).unlink()
    else:
        (target / "extra").write_bytes(b"extra")
    if mutation in {"file", "history_seal", "duplicate"}:
        reseal(target, document)
    with pytest.raises((SnapshotError, OSError)):
        verify_history_bundle(target, curated.directory)


def test_resource_failure_and_interrupted_write_cleanup_keep_output_immutable(
    native, tmp_path, monkeypatch
):
    _, curated, _, _ = native
    target = tmp_path / "history"
    with pytest.raises(SnapshotError, match="resource"):
        build_history_bundle(
            curated.directory, target, storage_policy=StoragePolicy(max_selected_rows=1)
        )
    real = bundle.write_private

    def failed(path, raw):
        raise OSError("interrupted")

    monkeypatch.setattr(bundle, "write_private", failed)
    with pytest.raises(OSError, match="interrupted"):
        build_history_bundle(curated.directory, target)
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(bundle, "write_private", real)
    document, _ = build_history_bundle(curated.directory, target)
    with pytest.raises(SnapshotError, match="conflict"):
        build_history_bundle(curated.directory, target, storage_policy=StoragePolicy(batch_rows=1))
    assert verify_history_bundle(target, curated.directory) == document
