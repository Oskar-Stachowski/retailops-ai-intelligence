"""Future facts/plans, causal revisions, physical pooling and supply censoring."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from retailops_ai.curated.builder import build_curated
from retailops_ai.source_snapshot.files import (
    MAX_METADATA_BYTES,
    SnapshotError,
    canonical_json,
    decode_json,
)
from retailops_ai.source_snapshot.importer import import_snapshot
from retailops_ai.stockout.artifacts import write_artifact
from retailops_ai.stockout.dataset import build_labels
from retailops_ai.stockout.feature_contract import FeaturePoint
from retailops_ai.stockout.feature_dataset import MAX_FEATURE_BYTES, build_features, verify_features
from retailops_ai.stockout.features import FEATURE_TABLES, feature_point
from retailops_ai.stockout.split import SplitPolicy, build_split, development_labels

ORIGIN = datetime(2026, 3, 28, 23, 59, 59, 999999, tzinfo=UTC)
OPENING = ORIGIN.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=28)


def row(available=OPENING, **values):
    body = {"curated_available_at": available, **values}
    return {
        **body,
        "source_record_sha256": hashlib.sha256(
            json.dumps(body, default=str, sort_keys=True).encode()
        ).hexdigest(),
    }


@pytest.fixture
def records():
    r = {name: [] for name in FEATURE_TABLES}
    begin, end = OPENING.date(), ORIGIN.date() + timedelta(days=10)
    r["product_catalog"] = [row(id="product", launch_date=begin, discontinue_date=None)]
    r["assortment"] = [
        row(
            product_id="product",
            selling_location_id="shop",
            channel="store",
            assortment_key="a",
            version=1,
            effective_from=begin,
            effective_to=end,
        )
    ]
    r["fulfillment_routes"] = [
        row(
            selling_location_id="shop",
            channel="store",
            stock_location_id="stock",
            route_key="route",
            version=1,
            effective_from=begin,
            effective_to=end,
        )
    ]
    r["inventory_ledger"] = [
        row(
            product_id="product",
            stock_location_id="stock",
            available_at=OPENING,
            occurred_at=OPENING,
            sequence=0,
            quantity_delta=156,
            movement_type="opening_stock",
        )
    ]
    for n in range(28):
        day = ORIGIN.date() - timedelta(days=27 - n)
        sold = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12)
        close = sold.replace(hour=0) + timedelta(days=1)
        r["inventory_ledger"].append(
            row(
                available=sold,
                product_id="product",
                stock_location_id="stock",
                available_at=sold,
                occurred_at=sold,
                sequence=n + 1,
                quantity_delta=-2,
                movement_type="sale",
            )
        )
        r["daily_demand_versions"].append(
            row(
                available=close,
                product_id="product",
                selling_location_id="shop",
                channel="store",
                version=1,
                business_date=day,
                observed_units=2,
                observation_status="observed_positive",
                mapped_stock_location_id="stock",
            )
        )
    r["inventory_history_coverage"] = [
        row(
            available=ORIGIN,
            product_id="product",
            stock_location_id="stock",
            covered_from_at=OPENING,
            covered_through_at=ORIGIN.replace(hour=0, minute=0, second=0, microsecond=0),
        )
    ]
    r["inventory_daily_snapshots"] = [
        row(
            available=ORIGIN,
            product_id="product",
            stock_location_id="stock",
            snapshot_at=ORIGIN,
            available_qty=100,
            on_hand=100,
            reserved_qty=0,
            status="known",
        )
    ]
    r["product_suppliers"] = [
        row(
            product_id="product",
            priority=1,
            product_supplier_id="supplier",
            effective_from=begin,
            effective_to=end,
            quoted_lead_time_days=3,
        )
    ]
    return r


def point(records, **kw):
    return feature_point(records, product="product", stock="stock", as_of=ORIGIN, **kw)


def test_known_sales_and_physical_stock_have_causal_counts(records):
    p = point(records)
    assert p.status == "eligible" and p.values.available_qty == 100
    assert p.values.history_known_days == 27 and p.values.history_missing_days == 1
    assert p.values.observed_sales_mean == 2.0 and p.values.days_of_supply_observed == 50.0
    assert p.values.history_in_stock_days == 27 and p.values.historical_stockout_onsets == 0
    assert p.feature_available_at == ORIGIN
    assert all(
        link.max_available_at is None or link.max_available_at <= ORIGIN for link in p.lineage
    )


@pytest.mark.parametrize(
    "table",
    [
        "inventory_ledger",
        "replenishment_receipts",
        "delivery_plan_versions",
        "daily_demand_versions",
    ],
)
def test_later_facts_and_revisions_cannot_rewrite_old_features(records, table):
    before = point(records)
    if table == "inventory_ledger":
        records[table].append(
            row(
                available=ORIGIN + timedelta(microseconds=1),
                occurred_at=ORIGIN + timedelta(seconds=1),
                quantity_delta=90000,
            )
        )
    elif table == "replenishment_receipts":
        records[table].append(
            row(
                available=ORIGIN + timedelta(microseconds=1),
                received_at=ORIGIN + timedelta(seconds=1),
                received_quantity=90000,
            )
        )
    elif table == "delivery_plan_versions":
        records[table].append(
            row(
                available=ORIGIN + timedelta(microseconds=1),
                version=900,
                expected_delivery_at=ORIGIN,
            )
        )
    else:
        records[table].append(
            {
                **records[table][0],
                "version": 2,
                "observed_units": 999999,
                "curated_available_at": ORIGIN + timedelta(microseconds=1),
            }
        )
    assert point(records) == before


def test_actual_future_receipt_is_not_an_early_delivery_feature(records):
    records["replenishment_orders"] = [
        row(
            product_id="product",
            stock_location_id="stock",
            replenishment_order_id="order",
            ordered_at=OPENING,
            ordered_quantity=20,
            expected_delivery_at=ORIGIN + timedelta(days=2),
        )
    ]
    records["delivery_plan_versions"] = [
        row(
            replenishment_order_id="order",
            version=1,
            expected_delivery_at=ORIGIN + timedelta(days=2),
            known_at=OPENING,
        )
    ]
    before = point(records)
    assert before.values.open_order_quantity == 20 and before.values.due_within_7d_quantity == 20
    records["replenishment_receipts"] = [
        row(
            available=ORIGIN + timedelta(days=1),
            received_at=ORIGIN + timedelta(days=1),
            replenishment_order_id="order",
            received_quantity=20,
        )
    ]
    assert point(records) == before
    records["delivery_plan_versions"].append(
        row(
            available=ORIGIN + timedelta(microseconds=1),
            replenishment_order_id="order",
            version=2,
            known_at=ORIGIN + timedelta(microseconds=1),
            expected_delivery_at=ORIGIN + timedelta(days=20),
        )
    )
    assert point(records) == before


def test_known_plan_revision_and_partial_receipt_change_only_known_open_supply(records):
    records["replenishment_orders"] = [
        row(
            product_id="product",
            stock_location_id="stock",
            replenishment_order_id="order",
            ordered_at=OPENING,
            ordered_quantity=20,
            expected_delivery_at=ORIGIN + timedelta(days=2),
        )
    ]
    records["delivery_plan_versions"] = [
        row(
            replenishment_order_id="order",
            version=1,
            known_at=OPENING,
            expected_delivery_at=ORIGIN + timedelta(days=2),
        ),
        row(
            available=ORIGIN,
            replenishment_order_id="order",
            version=2,
            known_at=ORIGIN,
            expected_delivery_at=ORIGIN + timedelta(days=10),
        ),
    ]
    records["replenishment_receipts"] = [
        row(
            available=ORIGIN,
            received_at=ORIGIN,
            replenishment_order_id="order",
            received_quantity=5,
        )
    ]
    p = point(records)
    assert p.values.open_order_quantity == 15 and p.values.due_within_7d_quantity == 0
    assert p.values.next_expected_delivery_hours == 240.0


def test_all_channels_pool_one_physical_position(records):
    records["assortment"].append(
        {
            **records["assortment"][0],
            "selling_location_id": "web",
            "channel": "online",
            "assortment_key": "web",
        }
    )
    records["fulfillment_routes"].append(
        {
            **records["fulfillment_routes"][0],
            "selling_location_id": "web",
            "channel": "online",
            "route_key": "web",
        }
    )
    records["daily_demand_versions"] += [
        {**r, "selling_location_id": "web", "channel": "online"}
        for r in records["daily_demand_versions"]
    ]
    p = point(records)
    assert p.values.available_qty == 100 and p.values.observed_sales_mean == 4.0
    assert "channel" not in p.model_dump()


def test_missing_one_channel_does_not_create_partial_zero_coverage(records):
    records["assortment"].append(
        {
            **records["assortment"][0],
            "selling_location_id": "web",
            "channel": "online",
            "assortment_key": "web",
        }
    )
    records["fulfillment_routes"].append(
        {
            **records["fulfillment_routes"][0],
            "selling_location_id": "web",
            "channel": "online",
            "route_key": "web",
        }
    )
    p = point(records)
    assert p.status == "insufficient_data" and p.values.history_known_days == 0
    assert p.values.observed_sales_mean is None and p.values.days_of_supply_observed is None


def test_unknown_snapshot_is_not_zero_and_stale_is_not_current(records):
    original = deepcopy(records)
    records["inventory_daily_snapshots"] = []
    p = point(records)
    assert p.reason == "inventory_unknown" and p.values.available_qty is None
    for offset, status in [
        (timedelta(days=1), "eligible"),
        (timedelta(days=1, microseconds=1), "insufficient_data"),
    ]:
        records = deepcopy(original)
        records["inventory_daily_snapshots"][0]["snapshot_at"] = ORIGIN - offset
        p = point(records)
        assert p.status == status


def test_zero_at_origin_is_current_stockout_even_with_little_history(records):
    records["inventory_daily_snapshots"][0].update(available_qty=0, on_hand=0)
    records["daily_demand_versions"] = []
    assert point(records).status == "already_stockout"


def test_newer_known_movements_update_an_older_snapshot(records):
    records["inventory_daily_snapshots"][0]["snapshot_at"] = ORIGIN - timedelta(hours=1)
    records["inventory_ledger"].append(
        row(
            available=ORIGIN,
            product_id="product",
            stock_location_id="stock",
            available_at=ORIGIN,
            occurred_at=ORIGIN,
            sequence=100,
            quantity_delta=-100,
            movement_type="sale",
        )
    )
    p = point(records)
    assert p.values.available_qty == 0 and p.status == "already_stockout"


def test_warehouse_separation_and_no_truth_table(records):
    before = point(records).values
    records["inventory_ledger"].append(
        row(product_id="product", stock_location_id="other", quantity_delta=999999)
    )
    assert point(records).values == before
    records["inventory_demand_outcomes"] = []
    with pytest.raises(ValueError, match="allowlisted"):
        point(records)


def test_censored_sales_ablation_excludes_instantaneous_stockout_days(records):
    stamp = OPENING + timedelta(days=10, hours=13)
    # Known physical inventory hits zero and is restored at exactly the same time.
    balance = 156 - 2 * 10
    for sequence, delta in [(100, -balance), (101, balance)]:
        records["inventory_ledger"].append(
            row(
                available=stamp,
                product_id="product",
                stock_location_id="stock",
                available_at=stamp,
                occurred_at=stamp,
                sequence=sequence,
                quantity_delta=delta,
                movement_type="inventory_adjustment",
            )
        )
    p = point(records)
    assert p.values.history_constrained_days == 1
    assert p.values.history_in_stock_days == 26 and p.values.historical_stockout_onsets == 1
    assert p.values.in_stock_sales_mean == 2.0


def test_zero_sales_rate_is_nullable_supply_not_infinity(records):
    for r in records["daily_demand_versions"]:
        r.update(observed_units=0, observation_status="observed_zero")
    p = point(records)
    assert p.values.observed_sales_mean == 0.0 and p.values.days_of_supply_observed is None


def test_metadata_cannot_claim_future_availability_or_wrong_history_counts(records):
    raw = point(records).model_dump(mode="json")
    raw["feature_available_at"] = (ORIGIN + timedelta(microseconds=1)).isoformat()
    with pytest.raises(ValidationError, match="after_origin"):
        FeaturePoint.model_validate_json(json.dumps(raw))
    raw = point(records).model_dump(mode="json")
    raw["values"]["history_known_days"] = 28
    with pytest.raises(ValidationError, match="history_counts"):
        FeaturePoint.model_validate_json(json.dumps(raw))


def test_larger_feature_artifact_has_explicit_bound_without_relaxing_metadata_limit():
    raw = canonical_json(dict(history="x" * MAX_METADATA_BYTES))
    with pytest.raises(SnapshotError, match="metadata_size_limit"):
        decode_json(raw)
    assert decode_json(raw, limit=MAX_FEATURE_BYTES)["history"] == "x" * MAX_METADATA_BYTES
    with pytest.raises(SnapshotError, match="metadata_size_limit"):
        decode_json(raw, limit=len(raw) - 1)


@pytest.mark.parametrize(
    "raw,reason",
    [
        (b'{"a":0,"a":1}', "duplicate_json_key"),
        (b'{"a":NaN}', "nonfinite_json_number"),
        (b"[]", "json_object_required"),
    ],
)
def test_feature_decoder_keeps_strict_json_validation(raw, reason):
    with pytest.raises(SnapshotError, match=reason):
        decode_json(raw, limit=MAX_FEATURE_BYTES)


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    root = tmp_path_factory.mktemp("stockout-feature-native").resolve()
    with ZipFile(Path(__file__).parents[1] / "data/fixtures/inventory-v1_1.zip") as archive:
        archive.extractall(root / "fixture")
    imported = import_snapshot(root / "fixture/facts", root / "data/generated")
    curated = build_curated(imported.directory, root / "data/generated")
    features = build_features(curated.directory)
    labels = build_labels(root / "fixture/private", allow_evaluation_truth=True)
    return root, curated, features, labels


def test_native_feature_full_replay_and_private_immutable_publication(native, tmp_path):
    _, curated, features, _ = native
    assert len(features["points"]) == 60
    assert (
        features["report"]["model_ready"] is False
        and features["report"]["upstream_forecast_ready"] is False
    )
    target = tmp_path / "features.json"
    assert write_artifact(features, target, max_bytes=MAX_FEATURE_BYTES) == "published"
    assert write_artifact(features, target, max_bytes=MAX_FEATURE_BYTES) == "reused"
    assert target.stat().st_mode & 0o777 == 0o600
    assert verify_features(target, curated.directory) == features
    corrupted = deepcopy(features)
    corrupted["points"][-1]["values"]["open_order_quantity"] += 1000
    corrupted["descriptor"]["points_sha256"] = hashlib.sha256(
        canonical_json(corrupted["points"])
    ).hexdigest()
    corrupted["feature_dataset_id"] = (
        "features-sha256-" + hashlib.sha256(canonical_json(corrupted["descriptor"])).hexdigest()
    )
    target2 = tmp_path / "resealed.json"
    target2.write_bytes(canonical_json(corrupted))
    with pytest.raises(SnapshotError, match="full_replay"):
        verify_features(target2, curated.directory)


def test_native_ten_day_fixture_does_not_pretend_to_support_temporal_training(native):
    _, _, features, labels = native
    policy = SplitPolicy(
        start_at="2026-07-22T00:00:00Z",
        train_until="2026-07-25T00:00:00Z",
        tune_until="2026-07-27T00:00:00Z",
        calibration_until="2026-07-29T00:00:00Z",
        test_until="2026-08-01T00:00:00Z",
        evaluated_at="2026-08-01T00:00:00Z",
    )
    split = build_split(features, labels, policy)
    assert split["report"]["temporal_membership_ready"] is False
    assert sum(split["report"]["eligible_by_role"].values()) == 0
    assert "test" not in split["report"]["development_classes"]
    with pytest.raises(ValueError, match="separate_approved_campaign"):
        development_labels(split, features, labels, role="test")
