"""Complete native public preparation/reload on exposed Source1.1/1.2 controls."""

import hashlib
import json
import shutil
from datetime import datetime

import pytest
from test_campaign_anomaly_parent import public_parent as public_parent  # noqa: F401
from test_campaign_anomaly_parent import source

from retailops_ai.curated.builder import iter_rows
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_stockout_parent_contract import (
    CampaignStockoutParentPlan,
)
from retailops_ai.evaluation_campaign.campaign_stockout_public import (
    prepare_public_stockout,
    verify_public_stockout,
)
from retailops_ai.evaluation_campaign.campaign_stockout_public_contract import (
    CampaignStockoutPublicPlan,
)
from retailops_ai.evaluation_campaign.campaign_stockout_upstream_facts import (
    CampaignStockoutUpstreamIndex,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout.features import FEATURE_TABLES
from retailops_ai.stockout.upstream import UPSTREAM_TABLES, upstream_point
from retailops_ai.stockout_history.projection import feature_point
from retailops_ai.stockout_preparation.index import FactIndex
from retailops_ai.stockout_training.inputs import category_at


def plan_for(case, **updates):
    snapshot, curated, _, _ = case
    return CampaignStockoutPublicPlan(
        parent=CampaignStockoutParentPlan(source=source(snapshot, curated), runtime=runtime_pin()),
        **updates,
    )


@pytest.fixture(scope="module")
def prepared(public_parent, tmp_path_factory):
    snapshot, curated, _, _ = public_parent
    root = tmp_path_factory.mktemp("ai09-stockout-public-full")
    target = root / "bundle"
    plan = plan_for(public_parent)
    manifest = prepare_public_stockout(snapshot, curated, target, plan)
    return target, plan, manifest


def test_all_origins_features_upstream_categories_and_reload_match_closed_native(
    public_parent, prepared
):
    target, plan, manifest = prepared
    _, curated, _, source_manifest = public_parent
    tables = tuple(sorted(set(FEATURE_TABLES) | set(UPSTREAM_TABLES)))
    records = {
        name: list(iter_rows(curated, spec["files"], 256))
        for name in tables
        for spec in source_manifest["tables"]
        if spec["table"] == name
    }
    physical = {name: records[name] for name in FEATURE_TABLES}
    native_index = FactIndex(physical)
    upstream_records = {name: records[name] for name in UPSTREAM_TABLES}
    expected = sorted(
        {
            (r["product_id"], r["stock_location_id"], r["snapshot_at"])
            for r in records["inventory_daily_snapshots"]
        }
    )
    rows = [json.loads(raw) for raw in (target / "rows.jsonl").read_bytes().splitlines()]
    assert len(rows) == len(expected) == manifest["descriptor"]["origins"]
    upstream = []
    for row, (product, stock, origin) in zip(rows, expected, strict=True):
        feature = feature_point(
            native_index.known(product, stock, origin), product=product, stock=stock, as_of=origin
        )
        forecast = upstream_point(upstream_records, product=product, stock=stock, as_of=origin)
        upstream.append(forecast.model_dump(mode="json"))
        category, category_hash = category_at(records["product_catalog"], product, origin)
        units, quantity = forecast.forecast_units_7d, feature.values.available_qty
        assert row == dict(
            product_id=product,
            stock_location_id=stock,
            as_of=feature.model_dump(mode="json")["as_of"],
            status=feature.status,
            reason=feature.reason,
            category_id=category,
            category_source_record_sha256=category_hash,
            values={
                **feature.values.model_dump(mode="json"),
                "forecast_units_7d": units,
                "forecast_days_of_supply": 7 * quantity / units
                if quantity is not None and units
                else None,
                "forecast_unavailable": int(forecast.status != "available"),
            },
            feature_sha256=canonical_sha256(feature.model_dump(mode="json")),
            upstream_sha256=canonical_sha256(forecast.model_dump(mode="json")),
        )
    upstream.sort(
        key=lambda p: (datetime.fromisoformat(p["as_of"]), p["product_id"], p["stock_location_id"])
    )
    assert (
        manifest["descriptor"]["upstream_native_points_sha256"]
        == hashlib.sha256(canonical_bytes(upstream)).hexdigest()
    )
    assert manifest["descriptor"]["upstream_stats"]["globally_validated_origins"] == len(
        {x[2].date() for x in expected}
    )
    assert (
        verify_public_stockout(
            target, expected_dataset_id=manifest["dataset_id"], expected_plan=plan
        )
        == manifest
    )
    assert not manifest["private_labels_read"] and not manifest["stage_ready"]
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize(
    "mutation", ["missing_last", "duplicate", "reordered", "resealed", "extra", "symlink"]
)
def test_complete_verifier_refuses_changed_population_and_resealed_artifact(
    prepared, tmp_path, mutation
):
    source, plan, manifest = prepared
    root = tmp_path / "copy"
    shutil.copytree(source, root)
    path = root / "rows.jsonl"
    rows = path.read_bytes().splitlines(keepends=True)
    if mutation == "missing_last":
        path.write_bytes(b"".join(rows[:-1]))
    elif mutation == "duplicate":
        path.write_bytes(b"".join([rows[0], *rows]))
    elif mutation == "reordered":
        path.write_bytes(b"".join(reversed(rows)))
    elif mutation == "extra":
        (root / "unexpected").write_bytes(b"x")
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to(source / "rows.jsonl")
    else:
        position = next(
            i
            for i, line in enumerate(rows)
            if json.loads(line)["values"]["available_qty"] is not None
        )
        row = json.loads(rows[position])
        row["values"]["available_qty"] += 1
        rows[position] = canonical_bytes(row) + b"\n"
        path.write_bytes(b"".join(rows))
        changed = json.loads((root / "manifest.json").read_bytes())
        changed["descriptor"]["rows_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        changed["dataset_id"] = "ai09-stockout-public-sha256-" + canonical_sha256(
            changed["descriptor"]
        )
        (root / "manifest.json").write_bytes(canonical_bytes(changed) + b"\n")
    with pytest.raises((SnapshotError, ValueError, OSError)):
        verify_public_stockout(root, expected_dataset_id=manifest["dataset_id"], expected_plan=plan)


def test_artifact_budget_does_not_publish_a_smaller_population(public_parent, tmp_path):
    snapshot, curated, _, _ = public_parent
    with pytest.raises(SnapshotError, match="artifact_budget"):
        prepare_public_stockout(
            snapshot, curated, tmp_path / "bundle", plan_for(public_parent, max_artifact_bytes=4096)
        )
    assert not list(tmp_path.iterdir())


def test_native_index_change_invalidates_whole_preparation_before_publication(
    public_parent, tmp_path, monkeypatch
):
    original = CampaignStockoutUpstreamIndex.known_series
    changed = False

    def corrupt(index, *args):
        nonlocal changed
        if not changed:
            changed = True
            db = index._connection()
            db.execute("PRAGMA query_only=OFF")
            db.execute(
                "UPDATE series_index SET product='changed' WHERE table_name='product_catalog'"
            )
            db.commit()
            db.execute("PRAGMA query_only=ON")
        return original(index, *args)

    monkeypatch.setattr(CampaignStockoutUpstreamIndex, "known_series", corrupt)
    snapshot, curated, _, _ = public_parent
    with pytest.raises(SnapshotError, match="database_changed"):
        prepare_public_stockout(snapshot, curated, tmp_path / "bundle", plan_for(public_parent))
    assert not list(tmp_path.iterdir())


def test_existing_target_never_overwrites_accepted_artifact(public_parent, prepared):
    target, plan, _ = prepared
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir()}
    snapshot, curated, _, _ = public_parent
    with pytest.raises(SnapshotError, match="new_separate_output"):
        prepare_public_stockout(snapshot, curated, target, plan)
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir()}
