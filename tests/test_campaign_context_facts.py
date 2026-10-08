"""Real frozen public 1.1/1.2 sources, exact AI08 projection and bounded failures.

No source is regenerated, no model is fitted, and no project final role is read.
The imported repository fixtures are already exposed control data.
"""

import shutil
from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from test_campaign_segments import inputs

from retailops_ai.curated.builder import build_curated, iter_rows
from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.evaluation_campaign.campaign_context_facts import CampaignContextFacts
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignContextStoragePolicy,
    CampaignForecastContextScope,
    CampaignForecastSegmentPolicy,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.importer import import_snapshot
from retailops_ai.stockout.features import FEATURE_TABLES
from retailops_ai.stockout_history.projection import feature_point
from retailops_ai.stockout_preparation.index import FactIndex


@pytest.fixture(scope="module", params=["inventory", "demand", "physical"])
def public_parent(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("ai09-context-public").resolve()
    name = "inventory-v1_1.zip" if request.param == "inventory" else "anomaly-v1_2.zip"
    member = "facts" if request.param == "inventory" else request.param + "/public"
    with ZipFile(Path(__file__).parents[1] / "data/fixtures" / name) as archive:
        # Extract only the previously exposed public input, without private truth.
        for info in archive.infolist():
            if info.filename.startswith(member + "/"):
                archive.extract(info, root / "fixture")
    imported = import_snapshot(root / "fixture" / member, root / "data/generated")
    curated = build_curated(imported.directory, root / "data/generated")
    records = {
        name: list(iter_rows(curated.directory, spec["files"], 256))
        for name in FEATURE_TABLES
        for spec in curated.manifest["tables"]
        if spec["table"] == name
    }
    policy = CampaignForecastSegmentPolicy(
        category_inventory=tuple(sorted({r["category_id"] for r in records["product_catalog"]}))
    )
    descriptor = curated.manifest["descriptor"]
    scope = CampaignForecastContextScope(
        data_seed=42,
        role="development_evaluation",
        dataset_id="ai09-physical-forecast-sha256-" + "a" * 64,
        source_recipe_sha256="b" * 64,
        source_dataset_id=descriptor["parent_source_dataset_id"],
        snapshot_id=descriptor["parent_snapshot_id"],
        curated_dataset_id=curated.manifest["curated_dataset_id"],
        source_scenario_plan_sha256=None,
        segment_policy_sha256=policy.content_sha256(),
    )
    return curated, records, policy, scope


def reader(public_parent, scratch, **updates):
    curated, _, _, scope = public_parent
    return CampaignContextFacts(
        curated.directory,
        scope=scope,
        policy=CampaignContextStoragePolicy(**updates),
        scratch=scratch,
    )


def feature_for(route, origin, *, horizon=1):
    origin = end_of_day(origin.date())
    feature, _ = inputs(horizon=horizon)
    target = origin.date() + timedelta(days=horizon)
    calendar = {
        "target_weekday": target.weekday(),
        "target_week_of_year": target.isocalendar().week,
        "target_month": target.month,
        "target_quarter": (target.month - 1) // 3 + 1,
        "target_is_weekend": target.weekday() >= 5,
        "channel": route["channel"],
    }
    values = []
    for v in feature.values:
        values.append(
            v.model_copy(
                update={
                    "source_available_at": origin,
                    "effective_date": target if v.kind != "observed" else None,
                    "observed_through_date": origin.date()
                    - timedelta(days=int(v.name.split("_")[2]) - 1)
                    if v.name.startswith("origin_lag_")
                    else origin.date()
                    if v.kind == "observed"
                    else None,
                    "value": calendar.get(v.name, v.value),
                }
            )
        )
    return feature.model_copy(
        update={
            "product_id": route["product_id"],
            "selling_location_id": route["selling_location_id"],
            "channel": route["channel"],
            "forecast_origin": origin,
            "target_date": target,
            "values": tuple(values),
        }
    )


def test_full_real_sources_keep_all_versions_and_exact_ai08_known_rows(public_parent, tmp_path):
    _, records, policy, _ = public_parent
    expected = FactIndex(records)
    with reader(public_parent, tmp_path) as facts:
        facts.check_categories(policy)
        assert facts.stats["stored_rows"] == sum(len(r) for r in records.values())
        origins = sorted(
            {
                (r["product_id"], r["stock_location_id"], r["snapshot_at"])
                for r in records["inventory_daily_snapshots"]
            }
        )
        for product, stock, origin in origins:
            assert facts.known(product, stock, origin) == expected.known(product, stock, origin)
        assert facts.path.stat().st_mode & 0o777 == 0o600
        assert facts.path.parent.stat().st_mode & 0o777 == 0o700
        assert facts.stats["maximum_selected_rows"] <= facts.policy.max_selected_rows
        assert not facts.seal["audited_source_read_proved_by_this_seal"]
        path = facts.path
    assert not path.parent.exists()
    with pytest.raises(SnapshotError, match="closed_unsealed_or_failed"):
        facts.known(*origins[-1])
    with pytest.raises(SnapshotError, match="single_use"):
        facts.__enter__()


def test_origin_route_and_point_are_derived_from_source_and_cached_for_horizons(
    public_parent, tmp_path
):
    _, records, _, _ = public_parent
    origin = end_of_day(max(r["snapshot_at"] for r in records["inventory_daily_snapshots"]).date())
    selling = records["assortment"][0]
    feature = feature_for(selling, origin)
    with reader(public_parent, tmp_path) as facts:
        route = facts.route(feature)
        assert route is not None and route.available_at <= origin
        point = facts.point(feature, route)
        assert point == feature_point(
            records, product=feature.product_id, stock=route.stock_location_id, as_of=origin
        )
        next_feature = feature_for(selling, origin, horizon=2)
        assert facts.point(next_feature, facts.route(next_feature)) == point
        assert facts.stats["origin_point_projections"] == 1
        earlier = feature_for(selling, origin - timedelta(days=1))
        earlier_route = facts.route(earlier)
        assert earlier_route is not None
        earlier_point = facts.point(earlier, earlier_route)
        assert earlier_point == feature_point(
            records,
            product=earlier.product_id,
            stock=earlier_route.stock_location_id,
            as_of=earlier.forecast_origin,
        )
        assert facts.stats["origin_point_projections"] == 2
        with pytest.raises(SnapshotError, match="does_not_match_source"):
            facts.point(feature, route.model_copy(update={"stock_location_id": "fabricated"}))
        with pytest.raises(SnapshotError, match="closed_unsealed_or_failed"):
            facts.route(feature)


@pytest.mark.parametrize(
    "limit,value", [("max_selected_rows", 1), ("max_selected_bytes", 1), ("max_index_bytes", 4096)]
)
def test_resource_failures_never_select_a_smaller_population(public_parent, tmp_path, limit, value):
    _, records, _, _ = public_parent
    r = records["inventory_daily_snapshots"][-1]
    with pytest.raises(SnapshotError, match="resource"):
        with reader(public_parent, tmp_path, **{limit: value}) as facts:
            facts.known(r["product_id"], r["stock_location_id"], r["snapshot_at"])
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mutation", ["payload", "index", "category"])
def test_private_mutation_is_rejected_even_with_valid_original_parent(
    public_parent, tmp_path, mutation
):
    _, records, policy, _ = public_parent
    r = records["inventory_daily_snapshots"][-1]
    with reader(public_parent, tmp_path) as facts:
        facts._db.execute("PRAGMA query_only=OFF")
        if mutation == "index":
            facts._db.execute("UPDATE facts SET ready='0000' WHERE table_name='product_catalog'")
        else:
            facts._db.execute(
                "UPDATE facts SET payload=? WHERE table_name='product_catalog'", (b"corrupt",)
            )
        with pytest.raises(SnapshotError, match="mismatch"):
            if mutation == "category":
                facts.check_categories(policy)
            else:
                facts.known(r["product_id"], r["stock_location_id"], r["snapshot_at"])


def test_complete_catalog_inventory_cannot_omit_a_difficult_category(public_parent, tmp_path):
    with reader(public_parent, tmp_path) as facts:
        with pytest.raises(SnapshotError, match="complete_category_inventory_mismatch"):
            facts.check_categories(CampaignForecastSegmentPolicy(category_inventory=("invented",)))


def test_change_to_an_unused_parent_table_is_caught_on_exit_and_scratch_is_removed(
    public_parent, tmp_path
):
    curated, _, _, scope = public_parent
    copied = tmp_path / "parent"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    shutil.copytree(curated.directory, copied)
    with pytest.raises(SnapshotError, match="complete_parent_changed"):
        with CampaignContextFacts(
            copied, scope=scope, policy=CampaignContextStoragePolicy(), scratch=scratch
        ) as facts:
            spec = next(
                t
                for t in facts.document["tables"]
                if t["table"] not in FEATURE_TABLES and t["files"]
            )
            path = copied / spec["files"][0]["path"]
            path.write_bytes(path.read_bytes() + b"changed")
    assert not list(scratch.iterdir())


def test_known_route_cannot_be_declared_missing_to_move_keys_into_unknown_inventory(
    public_parent, tmp_path
):
    _, records, _, _ = public_parent
    origin = end_of_day(max(r["snapshot_at"] for r in records["inventory_daily_snapshots"]).date())
    feature = feature_for(records["assortment"][0], origin)
    with reader(public_parent, tmp_path) as facts:
        assert facts.route(feature) is not None
        with pytest.raises(SnapshotError, match="does_not_match_source"):
            facts.point(feature, None)
        with pytest.raises(SnapshotError, match="closed_unsealed_or_failed"):
            facts.check_parent()


def test_deleted_private_facts_are_detected_before_a_complete_source_context_can_be_returned(
    public_parent, tmp_path
):
    with pytest.raises(SnapshotError, match="complete_private_index_mismatch"):
        with reader(public_parent, tmp_path) as facts:
            facts._db.execute("PRAGMA query_only=OFF")
            facts._db.execute(
                "DELETE FROM facts WHERE table_name='daily_demand_versions' AND position=0"
            )
            facts._db.commit()
    assert not list(tmp_path.iterdir())
