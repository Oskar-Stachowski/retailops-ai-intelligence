"""Real metric/cluster equations on explicit controlled data, no Source/model qualification."""

from copy import deepcopy
from datetime import date

import pytest
import test_campaign_segments as controls
from pydantic import ValidationError
from test_campaign_evaluation_data import evaluation_plan
from test_campaign_portfolio import document
from test_campaign_uncertainty import row as prediction

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
    FILES,
    CampaignContextBundleReceipt,
    CampaignContextBundleRecipe,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import CampaignPortfolioProtocol
from retailops_ai.evaluation_campaign.campaign_required_group_contract import (
    STRUCTURAL_DIAGNOSTICS,
    CampaignPortfolioRequiredGroupPolicy,
)
from retailops_ai.evaluation_campaign.campaign_robust_validation import validate_selected_robustness
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastSegmentCensus,
    required_population_ids,
)
from retailops_ai.evaluation_campaign.campaign_selected_robustness import SelectedRobustness
from retailops_ai.evaluation_campaign.campaign_uncertainty_contract import (
    CampaignForecastUncertaintyPolicy,
)
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.source_snapshot.files import SnapshotError, file_hash


def preregistration(root, base_document=None, segment_policy=None):
    value = deepcopy(base_document) if base_document is not None else document(root)
    segment = segment_policy or controls.policy()
    inventory = set(required_population_ids(segment))
    common = {g for g in inventory if g[0] in {"global", "horizon", "channel"}}
    common |= {("category", c) for c in segment.category_inventory}
    remaining = inventory - STRUCTURAL_DIAGNOSTICS - common
    owners = {
        "ordinary": {("scenario", "normal"), ("scenario", "promotion")},
        "physical": {
            ("scenario", "inventory_constraint"),
            ("anomaly", "return_spike"),
            ("anomaly", "inventory_censored_episode"),
        },
    }
    owners["demand"] = remaining - owners["ordinary"] - owners["physical"]
    original = CampaignPortfolioProtocol.model_validate_json(canonical_bytes(value))
    policy = CampaignPortfolioRequiredGroupPolicy.model_validate_json(
        canonical_bytes(
            {
                "segment_policy": segment.model_dump(mode="json"),
                "sources": [
                    {
                        "phase": s.phase,
                        "seed": s.seed,
                        "variant": s.variant,
                        "source_recipe_sha256": s.content_sha256(),
                        "required": [
                            {"dimension": d, "value": v}
                            for d, v in sorted(common | owners[s.variant])
                        ],
                    }
                    for s in original.sources
                ],
            }
        )
    )
    value["selection_policy_sha256"] = policy.content_sha256()
    value["segment_policy_sha256"] = segment.content_sha256()
    return CampaignPortfolioProtocol.model_validate_json(canonical_bytes(value)), policy


@pytest.mark.parametrize(
    "attack",
    [
        "source_missing",
        "source_duplicate",
        "source_hash_duplicate",
        "group_duplicate",
        "group_unsorted",
        "global_missing",
        "horizon_missing",
        "category_missing",
        "channel_missing",
        "cold_start_missing",
        "late_history_missing",
        "planned_anomaly_missing",
        "scenario_owner_missing",
        "unknown_group",
        "unknown_field",
    ],
)
def test_incomplete_critical_policy_rejected_before_source_io(tmp_path, attack):
    root = tmp_path / "never-created"
    _, policy = preregistration(root)
    value = policy.model_dump(mode="json")
    sources = value["sources"]
    if attack == "source_missing":
        sources.pop()
    elif attack == "source_duplicate":
        sources[-1] = deepcopy(sources[0])
    elif attack == "source_hash_duplicate":
        sources[-1]["source_recipe_sha256"] = sources[0]["source_recipe_sha256"]
    elif attack == "group_duplicate":
        sources[0]["required"].append(deepcopy(sources[0]["required"][0]))
    elif attack == "group_unsorted":
        sources[0]["required"].reverse()
    elif attack == "unknown_group":
        sources[0]["required"].append({"dimension": "volume", "value": "undeclared"})
        sources[0]["required"].sort(key=lambda g: (g["dimension"], g["value"]))
    elif attack == "unknown_field":
        value["relax_after_results"] = True
    else:
        identity = {
            "global_missing": ("global", "all"),
            "horizon_missing": ("horizon", "14"),
            "category_missing": ("category", "c2"),
            "channel_missing": ("channel", "online"),
            "cold_start_missing": ("history", "cold_start"),
            "late_history_missing": ("availability", "late_history"),
            "planned_anomaly_missing": ("anomaly", "multi_day_spike"),
            "scenario_owner_missing": ("scenario", "promotion"),
        }[attack]
        targets = (
            sources[:1]
            if attack
            in {"global_missing", "horizon_missing", "category_missing", "channel_missing"}
            else [s for s in sources if s["phase"] == "final" and s["seed"] == 137]
        )
        for source in targets:
            source["required"] = [
                g for g in source["required"] if (g["dimension"], g["value"]) != identity
            ]
    with pytest.raises(ValidationError):
        CampaignPortfolioRequiredGroupPolicy.model_validate_json(canonical_bytes(value))
    assert not root.exists()


def test_critical_coverage_preserved_for_every_seed(tmp_path):
    root = tmp_path / "never-created"
    protocol, policy = preregistration(root)
    assert len(policy.sources) == 12
    assert protocol.selection_policy_sha256 == policy.content_sha256()
    mandatory = set(required_population_ids(policy.segment_policy)) - STRUCTURAL_DIAGNOSTICS
    for phase, seed in (("development", 42), ("final", 42), ("final", 137), ("final", 2026)):
        assert mandatory <= {
            g.identity()
            for s in policy.sources
            if (s.phase, s.seed) == (phase, seed)
            for g in s.required
        }
    assert (
        not policy.native_scenario_effects_qualified
        and not policy.unknown_category_encoding_qualified
    )
    assert not policy.stage_ready and not root.exists()


@pytest.fixture(scope="module")
def controlled_report(tmp_path_factory, request):
    """Real streaming math/files; full Source parents and receipt are declared doubles."""
    root = tmp_path_factory.mktemp("required-groups")
    protocol, policy = preregistration(root / "never-created")
    phase, seed, variant, require_empty_cold = getattr(
        request, "param", ("development", 42, "ordinary", False)
    )
    source = next(
        s for s in protocol.sources if (s.phase, s.seed, s.variant) == (phase, seed, variant)
    )
    if require_empty_cold:
        fields = policy.model_dump(mode="json")
        own = next(
            s for s in fields["sources"] if s["source_recipe_sha256"] == source.content_sha256()
        )
        own["required"].append({"dimension": "history", "value": "cold_start"})
        own["required"].sort(key=lambda g: (g["dimension"], g["value"]))
        policy = type(policy).model_validate_json(canonical_bytes(fields))
        fields = protocol.model_dump(mode="json")
        fields["selection_policy_sha256"] = policy.content_sha256()
        protocol = type(protocol).model_validate_json(canonical_bytes(fields))
    policy.bind(protocol)
    segment = policy.segment_policy
    role = "final_test" if phase == "final" else "development_evaluation"
    scope = controls.scope(
        segment,
        source_recipe_sha256=source.content_sha256(),
        source_scenario_plan_sha256=source.scenario_plan_sha256,
        data_seed=seed,
        role=role,
        dataset_id="ai09-"
        + ("final" if phase == "final" else "physical")
        + "-forecast-sha256-"
        + "a" * 64,
    )
    uncertainty = CampaignForecastUncertaintyPolicy(
        resamples=199, minimum_eligible_time_blocks=2, minimum_eligible_series_clusters=2
    )
    plan = evaluation_plan(
        role=role,
        source_recipe_sha256=source.content_sha256(),
        segment_policy_sha256=segment.content_sha256(),
        uncertainty_policy_sha256=uncertainty.content_sha256(),
    )
    contexts = []
    with pytest.MonkeyPatch.context() as patch:
        for day in (date(2026, 3, 28), date(2026, 4, 28)):
            patch.setattr(controls, "ORIGIN", end_of_day(day))
            for product in range(60):
                category = "c1" if product < 30 else "c2"
                promotion = product % 2 == 0
                for channel in ("store", "online"):
                    for horizon in range(1, 15):
                        feature, history = controls.inputs(
                            product=f"product-{product:03d}", horizon=horizon, promotion=promotion
                        )
                        history = history.model_copy(
                            update={
                                "channel": channel,
                                "points": tuple(
                                    p.model_copy(update={"channel": channel})
                                    for p in history.points
                                ),
                            }
                        )
                        feature = feature.model_copy(
                            update={
                                "channel": channel,
                                "history_context_sha256": history.content_sha256(),
                                "values": tuple(
                                    v.model_copy(
                                        update={
                                            "value": category
                                            if v.name == "category_id"
                                            else channel
                                            if v.name == "channel"
                                            else v.value
                                        }
                                    )
                                    for v in feature.values
                                ),
                            }
                        )
                        annotation = controls.annotation(
                            feature, scope, scenario="promotion" if promotion else "normal"
                        )
                        contexts.append(
                            controls.context(
                                feature, history, p=segment, s=scope, active_annotation=annotation
                            )
                        )
    contexts.sort(key=membership_key)
    census = controls.census(contexts, p=segment, s=scope)
    bundle = root / "declared-context"
    bundle.mkdir(mode=0o700)
    (bundle / "contexts.jsonl").write_bytes(
        b"".join(canonical_bytes(c.model_dump(mode="json")) + b"\n" for c in contexts)
    )
    (bundle / "census.json").write_bytes(canonical_bytes(census.model_dump(mode="json")) + b"\n")
    for name in FILES - {"contexts.jsonl", "census.json"}:
        (bundle / name).write_bytes(canonical_bytes({"explicit_declared_parent": name}) + b"\n")
    for path in bundle.iterdir():
        path.chmod(0o600)
    files = {name: file_hash(bundle, name)[1] for name in FILES}
    size = sum(file_hash(bundle, name)[0] for name in FILES)
    recipe = CampaignContextBundleRecipe(
        phase=phase,
        role=role,
        source_recipe_sha256=source.content_sha256(),
        generation_operation_id="declared-generation",
        export_operation_id=plan.export_operation_id,
        segment_policy=segment,
        resources=plan.resources,
    )
    receipt = CampaignContextBundleReceipt(
        protocol_sha256=protocol.content_sha256(),
        operation_id="declared-context",
        reservation_id="campaign-operation-" + "a" * 32,
        recipe=recipe,
        generated_parent_receipt_sha256="a" * 64,
        generation_plan_sha256="a" * 64,
        export_receipt_sha256="a" * 64,
        runtime_code_sha256=protocol.runtime.code_sha256,
        scope=scope,
        rows=census.rows,
        eligible_rows=census.eligible_rows,
        keys_sha256=census.keys_sha256,
        eligible_keys_sha256=census.eligible_keys_sha256,
        role_population_sha256="a" * 64,
        context_trace_sha256=census.context_trace_sha256,
        census_sha256=census.content_sha256(),
        snapshot_inventory_sha256="a" * 64,
        curated_inventory_sha256="a" * 64,
        logical_curated_sha256="a" * 64,
        selection_sha256="b" * 64 if phase == "final" else None,
        artifact_sha256=canonical_sha256(files),
        artifact_bytes=size,
        artifact_files=files,
        worker_evidence={"controlled_parent": True},
        complete_export_role_file_passes=1 if phase == "final" else 6,
    )
    record = {"receipt": receipt.model_dump(mode="json"), "census": census.model_dump(mode="json")}
    population = {
        k: getattr(receipt, k)
        for k in (
            "rows",
            "eligible_rows",
            "keys_sha256",
            "eligible_keys_sha256",
            "role_population_sha256",
        )
    }
    indexes = root / "indexes"
    indexes.mkdir(mode=0o700)
    with SelectedRobustness(
        indexes,
        bundle,
        record,
        plan,
        population,
        uncertainty,
        retained_median_baseline=True,
        required_group_policy=policy,
        portfolio_protocol=protocol,
    ) as stream:
        for context in contexts:
            row = prediction(
                context.product_id,
                context.forecast_origin.date(),
                context.horizon_days,
                c=10.0,
                r=10.0,
                cb=(9.0, 11.0),
                rb=(9.0, 11.0),
            ).model_copy(
                update={
                    "channel": context.channel,
                    "selling_location_id": context.selling_location_id,
                    "example_sha256": context.example_sha256,
                    "frozen_configuration_sha256": plan.frozen_configuration_sha256,
                    "role": role,
                }
            )
            stream.add(row, 10)
        report = stream.finish()
    return protocol, policy, plan, record, population, uncertainty, report


def verify(case, report=None):
    protocol, policy, plan, record, population, uncertainty, original = case
    return validate_selected_robustness(
        report or original,
        record,
        plan,
        population,
        uncertainty,
        retained_median_baseline=True,
        required_group_policy=policy,
        portfolio_protocol=protocol,
    )


def test_real_critical_math_keeps_all_empty_diagnostics(controlled_report):
    report = controlled_report[-1]
    assert verify(controlled_report) is True
    groups = {(g["dimension"], g["value"]): g for g in report["groups"]}
    assert groups["global", "all"]["rows"] == 3360
    assert groups["scenario", "demand_shock"]["comparison"]["status"] == "not_ready"
    assert groups["scenario", "demand_shock"]["uncertainty"]["eligible_rows"] == 0
    assert len(groups) == 57 and report["quality_qualified"] is True
    _, _, plan, record, population, uncertainty, _ = controlled_report
    with pytest.raises(SnapshotError):
        validate_selected_robustness(
            report, record, plan, population, uncertainty, retained_median_baseline=True
        )


@pytest.mark.parametrize(
    "controlled_report",
    [
        ("final", seed, variant, False)
        for seed in (42, 137, 2026)
        for variant in ("ordinary", "demand", "physical")
    ],
    indirect=True,
    ids=[
        f"final-{seed}-{variant}"
        for seed in (42, 137, 2026)
        for variant in ("ordinary", "demand", "physical")
    ],
)
def test_final_core_uses_each_seed_and_original_source_owner_without_pooling(controlled_report):
    protocol, policy, plan, record, _, _, report = controlled_report
    census = CampaignForecastSegmentCensus.model_validate_json(canonical_bytes(record["census"]))
    source = next(s for s in protocol.sources if s.content_sha256() == plan.source_recipe_sha256)
    assert plan.phase == "final" and plan.role == "final_test"
    assert census.scope.data_seed == source.seed and source.phase == "final"
    assert verify(controlled_report) is (source.variant == "ordinary")
    assert len(report["groups"]) == 57
    assert policy.groups_for(protocol, plan, census) == frozenset(
        (g["dimension"], g["value"]) for g in report["required_groups"]
    )
    changed_seed = 137 if source.seed == 42 else 42
    wrong = census.model_copy(
        update={"scope": census.scope.model_copy(update={"data_seed": changed_seed})}
    )
    with pytest.raises(SnapshotError, match="frozen_protocol_or_source_mismatch"):
        policy.groups_for(protocol, plan, wrong)


@pytest.mark.parametrize(
    "controlled_report", [("development", 42, "ordinary", True)], indirect=True
)
def test_empty_preregistered_critical_group_blocks_otherwise_passing_math(controlled_report):
    report = controlled_report[-1]
    groups = {(g["dimension"], g["value"]): g for g in report["groups"]}
    assert groups["global", "all"]["comparison"]["status"] == "passed"
    assert groups["history", "cold_start"]["eligible_rows"] == 0
    assert {"dimension": "history", "value": "cold_start"} in report["required_groups"]
    assert report["quality_qualified"] is False and verify(controlled_report) is False


@pytest.mark.parametrize(
    "attack", ["drop_diagnostic", "alter_critical_inventory", "change_binding", "forge_quality"]
)
def test_flags_cannot_change_frozen_critical_ownership(controlled_report, attack):
    report = deepcopy(controlled_report[-1])
    if attack == "drop_diagnostic":
        report["groups"] = [g for g in report["groups"] if g["dimension"] != "anomaly"]
    elif attack == "alter_critical_inventory":
        report["required_groups"].pop()
    elif attack == "change_binding":
        report["required_group_policy_sha256"] = "0" * 64
    else:
        report["quality_qualified"] = False
    with pytest.raises(SnapshotError):
        verify(controlled_report, report)
