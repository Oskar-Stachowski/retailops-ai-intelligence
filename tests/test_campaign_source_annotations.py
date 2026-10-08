"""Existing exposed fixtures and controlled features; no Project or final reads.

The private fixture's plan is already exposed control metadata, recovered here
only to check its actual public Source hash. This is not preregistration-timing
proof. Effects are not passed to the component or used to choose any labels.
"""

import copy
import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from zipfile import ZipFile

import pytest
from test_campaign_segments import inputs, policy, scope

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationPlan,
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_source_annotations import SourceAnnotations
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.importer import verify_snapshot


@pytest.fixture(scope="module", params=["inventory", "demand", "physical"])
def source_case(request, tmp_path_factory):
    root = tmp_path_factory.mktemp("ai09-source-annotations").resolve()
    name = "inventory-v1_1.zip" if request.param == "inventory" else "anomaly-v1_2.zip"
    prefix = "facts" if request.param == "inventory" else request.param + "/public"
    with ZipFile(Path(__file__).parents[1] / "data/fixtures" / name) as archive:
        for item in archive.infolist():
            if item.filename.startswith(prefix + "/"):
                archive.extract(item, root)
        plan = (
            None
            if request.param == "inventory"
            else json.loads(
                archive.read(request.param + "/private/evaluation_truth/anomaly_scenario.json")
            )["plan"]
        )
    snapshot = verify_snapshot(
        root / prefix, required_use_cases=("forecast_source", "inventory_source"), scratch=root
    )
    source = snapshot.manifest["source"]
    context = scope(
        policy(),
        source_dataset_id=source["dataset_id"],
        snapshot_id=snapshot.snapshot_id,
        source_scenario_plan_sha256=source["descriptor"].get("scenario_plan_sha256"),
    )
    generation = CampaignGenerationPlan(
        source_recipe_sha256=context.source_recipe_sha256,
        exporter_lock_sha256="a" * 64,
        requested_parameters=source["requested_parameters"],
        resolved_parameters=source["descriptor"]["resolved_parameters"],
        entrypoint="cached_inventory_v2" if plan is None else "planned_anomaly",
        scenario_plan=plan,
        snapshot_schema_version=snapshot.manifest["schema_version"],
        required_use_cases=("forecast_source", "inventory_source")
        + (() if plan is None else ("anomaly_source",)),
        resources=CampaignGenerationResources(
            wall_seconds=1,
            tree_rss_bytes=1024,
            scratch_bytes=1024,
            minimum_free_disk_bytes=1024,
            minimum_available_memory_bytes=1024,
        ),
    )
    return snapshot, generation, context


def feature(window=None, *, day=None, horizon=1, promotion=False):
    row, _ = inputs(promotion=promotion)
    doc = row.model_dump(mode="python")
    target = day or (
        date.fromisoformat(window["start_date"]) if window is not None else row.target_date
    )
    origin = end_of_day(target - timedelta(days=horizon))
    delta = origin - row.forecast_origin
    if window is not None:
        doc.update({k: window[k] for k in ("product_id", "selling_location_id", "channel")})
    calendar = {
        "target_weekday": target.weekday(),
        "target_week_of_year": target.isocalendar().week,
        "target_month": target.month,
        "target_quarter": (target.month - 1) // 3 + 1,
        "target_is_weekend": target.weekday() >= 5,
        "channel": doc["channel"],
    }
    for value in doc["values"]:
        if value["source_available_at"] is not None:
            value["source_available_at"] += delta
        if value["observed_through_date"] is not None:
            value["observed_through_date"] += delta
        if value["effective_date"] is not None:
            value["effective_date"] = target
        if value["name"] in calendar:
            value["value"] = calendar[value["name"]]
    doc.update(forecast_origin=origin, target_date=target, horizon_days=horizon)
    return InputRow.model_validate(doc)


def test_actual_public_source_matches_frozen_plan_and_output_is_not_a_feature(source_case):
    snapshot, generation, context = source_case
    annotations = SourceAnnotations(snapshot, generation, context)
    row = feature()
    before = row.model_dump_json()
    result = annotations.annotation(row)
    assert result.context_scope_sha256 == context.content_sha256()
    assert result.source_scenario_plan_sha256 == context.source_scenario_plan_sha256
    assert result.data_class == "evaluation_annotation_never_model_input"
    assert result.effect_independence_claimed is False
    assert row.model_dump_json() == before
    assert "actual" not in result.model_dump() and "effects" not in result.model_dump()


def test_exact_target_dates_grains_and_all_declared_injection_types(source_case):
    snapshot, generation, context = source_case
    if generation.scenario_plan is None:
        pytest.skip("Unplanned fixture has no injection windows")
    annotations = SourceAnnotations(snapshot, generation, context)
    for window in generation.scenario_plan["injections"]:
        kind = window["injection_type"]
        row = feature(window, horizon=14)
        result = annotations.annotation(row)
        assert result.anomaly == kind
        assert result.scenario == (
            "inventory_constraint"
            if kind == "inventory_censored_episode"
            else "normal"
            if kind == "return_spike"
            else "demand_shock"
        )
        assert result.forecast_origin == row.forecast_origin
        # Origin alone is not the planned target-date label.
        wrong_grain = row.model_copy(update={"product_id": "other-product"})
        assert annotations.annotation(wrong_grain).anomaly == "unannotated"
        end = date.fromisoformat(window["end_date"])
        assert annotations.annotation(feature(window, day=end)).anomaly == kind
        assert annotations.annotation(feature(window, day=end + timedelta(days=1))).anomaly != kind
        assert (
            annotations.annotation(
                feature(window, day=date.fromisoformat(window["start_date"]) - timedelta(days=1))
            ).anomaly
            != kind
        )


def test_clean_control_and_known_offer_are_separate_from_future_effects(source_case):
    snapshot, generation, context = source_case
    annotations = SourceAnnotations(snapshot, generation, context)
    assert annotations.annotation(feature(promotion=True)).scenario == "promotion"
    assert annotations.annotation(feature(promotion=False)).scenario == "normal"
    if generation.scenario_plan is not None:
        for window in generation.scenario_plan["controls"]:
            result = annotations.annotation(feature(window))
            assert result.anomaly == (
                "clean_control" if window["control_type"] == "clean" else "unannotated"
            )
            assert result.scenario == "normal"


@pytest.mark.parametrize(
    "field", ["source_dataset_id", "snapshot_id", "source_recipe_sha256", "data_seed"]
)
def test_substituted_source_scope_is_rejected(source_case, field):
    snapshot, generation, context = source_case
    value = (
        137
        if field == "data_seed"
        else "0" * 64
        if field == "source_recipe_sha256"
        else ("source" if field == "source_dataset_id" else "snapshot") + "-sha256-" + "0" * 64
    )
    changed = context.model_copy(update={field: value})
    if field == "data_seed":
        # Correctly formed final scope isolates Source seed binding from role validation.
        changed = changed.model_copy(
            update={"role": "final_test", "dataset_id": "ai09-final-forecast-sha256-" + "a" * 64}
        )
    with pytest.raises(SnapshotError, match="scope_mismatch"):
        SourceAnnotations(snapshot, generation, changed)


@pytest.mark.parametrize("change", ["parameters", "private_source", "not_ready"])
def test_completed_public_source_metadata_is_required(source_case, change):
    snapshot, generation, context = source_case
    changed = copy.deepcopy(snapshot.manifest)
    if change == "parameters":
        changed["source"]["descriptor"]["resolved_parameters"]["days"] += 1
    elif change == "private_source":
        changed["descriptor"]["include_evaluation_truth"] = True
    else:
        changed["source"]["facts_ready"] = False
    with pytest.raises(SnapshotError, match="scope_mismatch"):
        SourceAnnotations(replace(snapshot, manifest=changed), generation, context)


@pytest.mark.parametrize("change", ["plan", "schema", "seed", "scope_hash"])
def test_plan_seed_schema_and_scope_hash_cannot_be_substituted(source_case, change):
    snapshot, generation, context = source_case
    if generation.scenario_plan is None:
        pytest.skip("Unplanned fixture has no plan")
    doc = generation.model_dump(mode="python")
    if change == "plan":
        doc["scenario_plan"]["injections"][0]["magnitude"] = (
            "1.7"
            if doc["scenario_plan"]["contract_version"].startswith("business-anomaly-")
            else 4
            if doc["scenario_plan"]["injections"][0]["injection_type"]
            == "inventory_censored_episode"
            else "1.7"
        )
    elif change == "schema":
        changed = copy.deepcopy(snapshot.manifest)
        changed["source"]["descriptor"]["scenario_schema_sha256"] = "0" * 64
        snapshot = replace(snapshot, manifest=changed)
    elif change == "seed":
        doc["scenario_plan"]["seed"] = 137
    else:
        context = context.model_copy(update={"source_scenario_plan_sha256": "0" * 64})
    with pytest.raises(SnapshotError, match="hash_mismatch"):
        SourceAnnotations(snapshot, CampaignGenerationPlan.model_validate(doc), context)


def test_array_order_matches_native_sorted_identity_and_caller_mutation_is_isolated(source_case):
    snapshot, generation, context = source_case
    if generation.scenario_plan is None:
        pytest.skip("Unplanned fixture has no plan")
    doc = generation.model_dump(mode="python")
    doc["scenario_plan"]["injections"].reverse()
    doc["scenario_plan"]["controls"].reverse()
    reordered = CampaignGenerationPlan.model_validate(doc)
    annotations = SourceAnnotations(snapshot, reordered, context)
    row = feature(generation.scenario_plan["injections"][0])
    before = annotations.annotation(row)
    reordered.scenario_plan["injections"][0]["start_date"] = "2000-01-01"
    assert annotations.annotation(row) == before
    assert annotations.plan_sha256 == context.source_scenario_plan_sha256
