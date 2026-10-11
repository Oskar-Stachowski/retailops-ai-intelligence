"""All physical origins on real exposed 1.1/1.2 public controls; no Project reads."""

import hashlib
import shutil

import pytest
from test_campaign_anomaly_parent import public_parent as public_parent  # noqa: F401
from test_campaign_anomaly_parent import source

from retailops_ai.curated.builder import iter_rows
from retailops_ai.evaluation_campaign.campaign_segment_contract import CampaignContextStoragePolicy
from retailops_ai.evaluation_campaign.campaign_stockout_parent import CampaignStockoutPublicParent
from retailops_ai.evaluation_campaign.campaign_stockout_parent_contract import (
    CampaignStockoutParentPlan,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.stockout.features import FEATURE_TABLES
from retailops_ai.stockout_history.projection import feature_point
from retailops_ai.stockout_preparation.index import FactIndex
from retailops_ai.stockout_training.inputs import category_at


def reader(case, scratch, **updates):
    snapshot, curated, _, _ = case
    plan = CampaignStockoutParentPlan(
        **{"source": source(snapshot, curated), "runtime": runtime_pin(), **updates}
    )
    return CampaignStockoutPublicParent(snapshot, curated, plan, scratch)


def test_every_real_physical_origin_matches_closed_native_projection_and_pit_category(
    public_parent, tmp_path
):
    _, curated, _, manifest = public_parent
    records = {
        name: list(iter_rows(curated, spec["files"], 256))
        for name in FEATURE_TABLES
        for spec in manifest["tables"]
        if spec["table"] == name
    }
    index = FactIndex(records)
    origins = sorted(
        {
            (r["product_id"], r["stock_location_id"], r["snapshot_at"])
            for r in records["inventory_daily_snapshots"]
        }
    )
    adapter = reader(public_parent, tmp_path)
    trace = hashlib.sha256()
    with adapter:
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
        rows = adapter.rows()
        for product, stock, origin in origins:
            row = next(rows)
            point = feature_point(
                index.known(product, stock, origin), product=product, stock=stock, as_of=origin
            )
            category, category_hash = category_at(records["product_catalog"], product, origin)
            assert row == dict(
                feature=point.model_dump(mode="json"),
                category_id=category,
                category_source_record_sha256=category_hash,
            )
            trace.update(canonical_json(row) + b"\n")
            # A consumer may modify its copy without changing replay or receipt.
            row["category_id"] = "consumer-modified"
        with pytest.raises(StopIteration):
            next(rows)
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
    receipt = adapter.receipt()
    assert receipt["origins"] == len(origins)
    assert sum(receipt["feature_statuses"].values()) == len(origins)
    assert receipt["feature_trace_sha256"] == trace.hexdigest()
    assert receipt["public_source_and_curated_replayed"]
    assert not receipt["private_labels_read"] and not receipt["stage_ready"]
    receipt["origins"] = -1
    assert adapter.receipt()["origins"] == len(origins)
    with pytest.raises(SnapshotError, match="closed_or_failed"):
        next(adapter.rows())
    with pytest.raises(SnapshotError, match="single_use"):
        with adapter:
            pass
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("consumption", ["none", "one", "second_stream"])
def test_partial_or_repeated_stream_never_returns_receipt(public_parent, tmp_path, consumption):
    adapter = reader(public_parent, tmp_path)
    with pytest.raises(SnapshotError, match="complete_origin_stream_required|stream_single_use"):
        with adapter:
            if consumption != "none":
                first = adapter.rows()
                next(first)
            if consumption == "second_stream":
                next(adapter.rows())
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("budget", ["origins", "selected", "runtime"])
def test_resource_or_runtime_failure_refuses_full_population_instead_of_sampling(
    public_parent, tmp_path, budget
):
    updates = {}
    if budget == "origins":
        updates["max_origins"] = 1
    elif budget == "selected":
        updates["storage"] = CampaignContextStoragePolicy(max_selected_rows=1)
    else:
        updates["runtime"] = runtime_pin().model_copy(update={"code_sha256": "f" * 64})
    adapter = reader(public_parent, tmp_path, **updates)
    with pytest.raises((SnapshotError, ValueError)):
        with adapter:
            for _ in adapter.rows():
                pass
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(tmp_path.iterdir())


def test_private_missing_origin_cannot_produce_complete_receipt(public_parent, tmp_path):
    adapter = reader(public_parent, tmp_path)
    with pytest.raises(SnapshotError, match="private_index_mismatch"):
        with adapter:
            db = adapter._facts._connection()
            db.execute("PRAGMA query_only=OFF")
            db.execute(
                "DELETE FROM facts WHERE table_name='inventory_daily_snapshots' AND position=0"
            )
            db.commit()
            db.execute("PRAGMA query_only=ON")
            for _ in adapter.rows():
                pass
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(tmp_path.iterdir())


def test_changed_unused_original_parent_after_consumption_invalidates_receipt(
    public_parent, tmp_path
):
    snapshot, curated, native, manifest = public_parent
    copied, scratch = tmp_path / "curated", tmp_path / "scratch"
    shutil.copytree(curated, copied)
    scratch.mkdir()
    adapter = reader((snapshot, copied, native, manifest), scratch)
    with pytest.raises(SnapshotError):
        with adapter:
            for _ in adapter.rows():
                pass
            spec = next(
                s for s in manifest["tables"] if s["table"] not in FEATURE_TABLES and s["row_count"]
            )
            path = copied / spec["files"][0]["path"]
            path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(scratch.iterdir())
