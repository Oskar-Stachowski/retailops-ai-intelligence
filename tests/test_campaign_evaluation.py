"""Real journals and role indexes with declared parent/model/monitor controls.

These prove audited ordering and durable evidence, not project fits or native
TensorFlow acceptance. Recipe freezing precedes creation of result receipts.
"""

import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_ai09_campaign_journal import protocol_document
from test_ai09_campaign_journal import selection as declared_selection
from test_campaign_evaluation_configuration import parents as declared_parents
from test_campaign_evaluation_configuration import trial_rows
from test_campaign_evaluation_data import evaluation_plan
from test_campaign_tune_worker import controlled_export
from test_forecast_features import SERIES
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_evaluation as runner
from retailops_ai.evaluation_campaign import campaign_evaluation_data as data
from retailops_ai.evaluation_campaign import campaign_evaluation_worker as worker
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_calibration import validate_completed_calibration
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_evaluation_configuration import (
    bind_forecast_configuration,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_receipt import (
    CampaignForecastEvaluationRecipe,
)
from retailops_ai.evaluation_campaign.campaign_export import _store_receipt
from retailops_ai.evaluation_campaign.campaign_fit import validate_completed_fit
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_tune import validate_completed_tune
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.source_snapshot.files import SnapshotError


def evaluation_recipe(frozen=None, **changes):
    prototype = evaluation_plan()
    fields = prototype.model_dump(mode="json", exclude={"version", "frozen_configuration_sha256"})
    fields |= {
        "tune_operation_id": "selected-tune",
        "calibration_operation_id": "frozen-calibration",
        "tune_score_operation_ids": ["tune-0", "tune-1"],
    }
    if frozen:
        fields |= {
            "tune_operation_id": frozen.tune_operation_id,
            "calibration_operation_id": frozen.calibration_operation_id,
            "tune_score_operation_ids": [t.tune_score_operation_id for t in frozen.trials],
            "source_recipe_sha256": frozen.development_source_recipe_sha256,
            "worker_environment_lock_sha256": frozen.worker_environment_lock_sha256,
        }
    return CampaignForecastEvaluationRecipe.model_validate_json(canonical_bytes(fields | changes))


@pytest.fixture
def control(stored_control, population, timeline, tmp_path, monkeypatch, request):
    dataset, manifest = stored_control
    root = tmp_path / "evaluation-journal"
    document = protocol_document(root)
    source = canonical_sha256(document["sources"][0])
    runtime = document["runtime"]["code_sha256"]
    old_tune, old_calibration, old_tune_scores, old_scores, old_fits = declared_parents()
    exported = controlled_export(dataset, manifest, source)
    exported = exported.model_copy(
        update={
            "plan": exported.plan.model_copy(
                update={"generation_operation_id": "development-42-generate"}
            ),
        }
    )
    document["use_case_quality_policy_sha256"]["forecast"] = (
        old_tune.plan.forecast_quality_policy_sha256
    )
    document["selection_policy_sha256"] = old_tune.plan.campaign_selection_policy_sha256
    context_recipe = uncertainty_policy = None
    if getattr(request, "param", None) == "robust":
        from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
            CampaignContextBundleRecipe,
        )
        from retailops_ai.evaluation_campaign.campaign_segment_contract import (
            CampaignForecastSegmentPolicy,
        )
        from retailops_ai.evaluation_campaign.campaign_uncertainty_contract import (
            CampaignForecastUncertaintyPolicy,
        )

        categories = tuple(
            sorted(
                {
                    value.value
                    for row in population[1]
                    for value in row.values
                    if value.name == "category_id" and value.value is not None
                }
            )
        )
        segment_policy = CampaignForecastSegmentPolicy(category_inventory=categories)
        uncertainty_policy = CampaignForecastUncertaintyPolicy()
        document["segment_policy_sha256"] = segment_policy.content_sha256()
        document["uncertainty_policy_sha256"] = uncertainty_policy.content_sha256()
        context_recipe = CampaignContextBundleRecipe(
            phase="development",
            role="development_evaluation",
            source_recipe_sha256=source,
            generation_operation_id="development-42-generate",
            export_operation_id=exported.operation_id,
            segment_policy=segment_policy,
            resources=evaluation_recipe().resources,
        )
    plans = {
        key: fit.plan.model_copy(
            update={"source_recipe_sha256": source, "export_operation_id": exported.operation_id}
        )
        for key, fit in old_fits.items()
    }
    score_plans = {
        score.operation_id: score.plan.model_copy(
            update={"source_recipe_sha256": source, "export_operation_id": exported.operation_id}
        )
        for score in (*old_tune_scores, *old_scores)
    }
    tune_plan = old_tune.plan.model_copy(
        update={"source_recipe_sha256": source, "export_operation_id": exported.operation_id}
    )
    calibration_plan = old_calibration.plan.model_copy(
        update={"source_recipe_sha256": source, "export_operation_id": exported.operation_id}
    )
    recipe = evaluation_recipe(
        source_recipe_sha256=source,
        export_operation_id=exported.operation_id,
        segment_policy_sha256=document["segment_policy_sha256"],
        uncertainty_policy_sha256=document["uncertainty_policy_sha256"],
    )
    # The full protocol, including this result-independent recipe, is frozen
    # before the protocol-bound export/fit/Tune/Cal result receipts are built.
    operations = [document["operations"][0]]

    def operation(key, action, role, plan, prerequisites, **extras):
        operations.append(
            {
                "operation_id": key,
                "phase": "development",
                "action": action,
                "use_case": "source" if action == "source_read" else "forecast",
                "role": role,
                "source_recipe_sha256": source,
                "execution_recipe_sha256": plan.content_sha256(),
                "prerequisites": prerequisites,
                "maximum_attempts": 1,
                **extras,
            }
        )

    operation(
        exported.operation_id,
        "source_read",
        "all_parent_data",
        exported.plan,
        ["development-42-generate"],
    )
    if context_recipe is not None:
        operation(
            "declared-context",
            "source_read",
            "all_parent_data",
            context_recipe,
            ["development-42-generate", exported.operation_id],
        )
    for key, plan in plans.items():
        operation(
            key,
            "model_fit",
            "train",
            plan,
            [exported.operation_id],
            forecast_family=plan.family,
            initialization_seed=plan.initialization_seed,
        )
    for score in old_tune_scores:
        operation(
            score.operation_id,
            "model_score",
            "tune",
            score_plans[score.operation_id],
            [exported.operation_id, *score.plan.fit_operation_ids.values()],
        )
    operation(
        old_tune.operation_id,
        "model_score",
        "tune",
        tune_plan,
        [exported.operation_id, *tune_plan.score_operation_ids],
    )
    for score in old_scores:
        operation(
            score.operation_id,
            "model_score",
            "calibration",
            score_plans[score.operation_id],
            [exported.operation_id, *score.plan.fit_operation_ids.values(), old_tune.operation_id],
        )
    operation(
        old_calibration.operation_id,
        "calibrator_fit",
        "calibration",
        calibration_plan,
        [exported.operation_id, old_tune.operation_id, *calibration_plan.score_operation_ids],
    )
    operation(
        "independent-evaluate",
        "model_score",
        "development_evaluation",
        recipe,
        [exported.operation_id, old_tune.operation_id, old_calibration.operation_id]
        + (["declared-context"] if context_recipe is not None else []),
    )
    operations += [o for o in document["operations"] if o["phase"] == "final"]
    document["operations"] = operations
    document["maximum_new_attempts"] = sum(o.get("maximum_attempts", 1) for o in operations)
    document["maximum_new_fit_attempts"] = sum(
        o.get("maximum_attempts", 1)
        for o in operations
        if o["action"] in ("model_fit", "calibrator_fit")
    )
    protocol = CampaignProtocol.model_validate_json(canonical_bytes(document))
    journal.initialize(root, protocol)
    frozen_protocol_bytes = (root / "journal.json").read_bytes()
    event = journal.reserve(root, "development-42-generate")
    journal.finish(
        root,
        str(event.reservation_id),
        result="completed",
        evidence_sha256=canonical_sha256("declared-generation-not-native"),
        cost=CampaignCost(wall_seconds=0.01),
    )

    def complete(receipt, **updates):
        start = journal.reserve(root, receipt.operation_id)
        result = receipt.model_copy(
            update={
                "protocol_sha256": protocol.content_sha256(),
                "runtime_code_sha256": runtime,
                "reservation_id": str(start.reservation_id),
                **updates,
            }
        )
        _store_receipt(root, result)
        size = (
            result.model_artifact_bytes
            if hasattr(result, "model_artifact_bytes")
            else result.artifact_bytes
        )
        journal.finish(
            root,
            str(start.reservation_id),
            result="completed",
            evidence_sha256=result.content_sha256(),
            cost=CampaignCost(
                wall_seconds=0.01, peak_process_tree_rss_bytes=12345, artifact_bytes=size
            ),
        )
        return result

    exported = complete(exported)
    fits = {
        key: complete(
            fit,
            plan=plans[key],
            dataset_id=exported.dataset_id,
            export_receipt_sha256=exported.content_sha256(),
        )
        for key, fit in old_fits.items()
    }

    def score(receipt):
        return complete(
            receipt,
            plan=score_plans[receipt.operation_id],
            dataset_id=exported.dataset_id,
            export_receipt_sha256=exported.content_sha256(),
            fit_receipt_sha256={
                f: fits[receipt.plan.fit_operation_ids[f]].content_sha256()
                for f in receipt.plan.fit_operation_ids
            },
        )

    tune_scores = tuple(score(s) for s in old_tune_scores)
    tune = complete(
        old_tune,
        plan=tune_plan,
        dataset_id=exported.dataset_id,
        export_receipt_sha256=exported.content_sha256(),
        score_receipt_sha256={s.operation_id: s.content_sha256() for s in tune_scores},
    )
    scores = tuple(score(s) for s in old_scores)
    calibration = complete(
        old_calibration,
        plan=calibration_plan,
        dataset_id=exported.dataset_id,
        export_receipt_sha256=exported.content_sha256(),
        tune_receipt_sha256=tune.content_sha256(),
        score_receipt_sha256={s.operation_id: s.content_sha256() for s in scores},
    )
    frozen = bind_forecast_configuration(
        tune,
        calibration,
        tune_scores,
        scores,
        fits,
        feature_schema_sha256=canonical_sha256(InputRow.model_json_schema()),
        quality_policy=recipe.quality_policy,
    )
    tune_bundle, calibration_bundle = tmp_path / "tune", tmp_path / "calibration"
    tune_bundle.mkdir(mode=0o700)
    calibration_bundle.mkdir(mode=0o700)
    write(
        calibration_bundle / "parents.json",
        {
            "tune_scores": {s.operation_id: s.model_dump(mode="json") for s in tune_scores},
            "scores": {s.operation_id: s.model_dump(mode="json") for s in scores},
        },
    )
    output = tmp_path / "evaluations"
    output.mkdir(mode=0o700)
    calls = []

    def reserved():
        event = journal.inspect(root).events[-1]
        assert event.kind == "reserved" and event.operation_id == "independent-evaluate"

    def verify_tune(bundle, *, journal, receipt):
        reserved()
        calls.append("tune-parent")
        validate_completed_tune(journal, receipt)

    def verify_calibration(bundle, *, journal, receipt):
        reserved()
        calls.append("calibration-parent")
        validate_completed_calibration(journal, receipt)

    def verify_fit(bundle, *, journal, receipt):
        reserved()
        calls.append("fit-parent")
        validate_completed_fit(journal, receipt)

    monkeypatch.setattr(runner, "verify_campaign_forecast_selection", verify_tune)
    monkeypatch.setattr(runner, "verify_campaign_forecast_calibration", verify_calibration)
    monkeypatch.setattr(runner, "verify_campaign_forecast_bundle", verify_fit)
    _, rows, _ = population
    histories = {
        r.history_context_sha256: OriginFeatures(
            timeline, make_origin(r.forecast_origin.date())
        ).history(SERIES)
        for r in rows
    }
    monkeypatch.setattr(
        data,
        "input_models",
        lambda path, name: iter(rows if name == "features" else histories.values()),
    )
    monkeypatch.setattr(
        worker,
        "verify_feature_set",
        lambda path: SimpleNamespace(
            feature_set_id=manifest.descriptor.feature_set_id,
            descriptor=manifest.descriptor.feature_descriptor,
        ),
    )

    def models(request):
        index = recipe.tune_score_operation_ids.index(request["trial"]["tune_score_operation_id"])
        return {}, {"index": index}

    def infer(windows, encodings, models):
        return {
            record.key: trial_rows(eligible=record.eligible)[models["index"]].values
            for window in windows
            for record in window.records
        }

    monkeypatch.setattr(worker, "load_models", models)
    monkeypatch.setattr(worker, "infer_functionals", infer)

    def monitored(command, **kwargs):
        reserved()
        phase, folder = command[-2], kwargs["root"]
        calls.append(phase)
        request = read(folder / "request.json")
        if phase == "predict":
            assert "actuals" not in request and "dataset" not in request
            assert not {"raw_context", "raw_context_bundle", "uncertainty_policy"}.intersection(
                request
            )
            assert command[0].endswith("declared-tf-worker")
        result = getattr(worker, phase)(folder, request, recipe.resolve(frozen))
        write(
            folder / (phase + ".json"),
            result
            | {
                "phase": phase,
                "worker_peak_rss_bytes": 12345,
                "worker_seconds": 0.01,
                "worker_cpu_seconds": 0.01,
            },
        )
        return {"status": "passed", "reason": None, "sampled_tree_peak_rss_bytes": 12345}

    monkeypatch.setattr(runner, "monitor", monitored)
    return {
        "root": root,
        "protocol": protocol,
        "frozen_protocol_bytes": frozen_protocol_bytes,
        "dataset": dataset,
        "recipe": recipe,
        "exported": exported,
        "configuration": frozen,
        "tune": tune,
        "calibration": calibration,
        "fits": fits,
        "tune_bundle": tune_bundle,
        "calibration_bundle": calibration_bundle,
        "fit_bundles": {key: tmp_path / key for key in fits},
        "output_root": output,
        "worker_python": tmp_path / "declared-tf-worker",
        "calls": calls,
        "context_recipe": context_recipe,
        "uncertainty_policy": uncertainty_policy,
    }


def execute(control, **changes):
    fields = {
        key: value
        for key, value in control.items()
        if key
        in {
            "dataset",
            "recipe",
            "exported",
            "configuration",
            "tune",
            "calibration",
            "fits",
            "tune_bundle",
            "calibration_bundle",
            "fit_bundles",
            "output_root",
            "worker_python",
        }
    }
    return runner.evaluate_campaign_forecast(
        **(fields | changes), journal=control["root"], operation_id="independent-evaluate"
    )


def test_preregistered_recipe_has_no_result_hash_cycle_and_resolves_all_bound_trials(control):
    recipe = control["recipe"]
    assert "frozen_configuration_sha256" not in type(recipe).model_fields
    assert "configuration" not in type(recipe).model_fields
    protocol = control["protocol"]
    assert control["configuration"].protocol_sha256 == protocol.content_sha256()
    operation = next(o for o in protocol.operations if o.operation_id == "independent-evaluate")
    assert operation.execution_recipe_sha256 == recipe.content_sha256()
    resolved = recipe.resolve(control["configuration"])
    assert resolved.frozen_configuration_sha256 == control["configuration"].content_sha256()
    assert recipe.content_sha256() != resolved.content_sha256()


def unregistered_context_receipt(control):
    """Valid controlled metadata, with no completed Source context operation."""
    from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
        FILES,
        CampaignContextBundleReceipt,
        CampaignContextBundleRecipe,
    )
    from retailops_ai.evaluation_campaign.campaign_segment_contract import (
        CampaignForecastContextScope,
        CampaignForecastSegmentPolicy,
    )

    exported = control["exported"]
    parent = exported.recipe.source.parent
    policy = CampaignForecastSegmentPolicy(category_inventory=("c1", "c2"))
    recipe = CampaignContextBundleRecipe(
        phase="development",
        role="development_evaluation",
        source_recipe_sha256=control["recipe"].source_recipe_sha256,
        generation_operation_id="development-42-generate",
        export_operation_id=exported.operation_id,
        segment_policy=policy,
        resources=control["recipe"].resources,
    )
    scope = CampaignForecastContextScope(
        data_seed=42,
        role=recipe.role,
        dataset_id=exported.dataset_id,
        source_recipe_sha256=recipe.source_recipe_sha256,
        source_dataset_id=parent.source_dataset_id,
        curated_dataset_id=parent.curated_dataset_id,
        snapshot_id=parent.snapshot_id,
        source_scenario_plan_sha256=None,
        segment_policy_sha256=policy.content_sha256(),
    )
    hashes = {name: "0" * 64 for name in FILES}
    return CampaignContextBundleReceipt(
        protocol_sha256=control["protocol"].content_sha256(),
        operation_id="unregistered-context",
        reservation_id="campaign-operation-" + "0" * 32,
        recipe=recipe,
        generated_parent_receipt_sha256="0" * 64,
        generation_plan_sha256="0" * 64,
        export_receipt_sha256=exported.content_sha256(),
        runtime_code_sha256=exported.runtime_code_sha256,
        scope=scope,
        rows=1,
        eligible_rows=0,
        keys_sha256="0" * 64,
        eligible_keys_sha256="0" * 64,
        role_population_sha256="0" * 64,
        context_trace_sha256="0" * 64,
        census_sha256="0" * 64,
        snapshot_inventory_sha256="0" * 64,
        curated_inventory_sha256="0" * 64,
        logical_curated_sha256="0" * 64,
        selection_sha256=None,
        artifact_sha256=canonical_sha256(hashes),
        artifact_bytes=1,
        artifact_files=hashes,
        worker_evidence={"controlled_metadata_only": True},
        complete_export_role_file_passes=6,
    )


@pytest.mark.parametrize("case", ["missing-bundle", "missing-receipt", "unregistered", "overlap"])
def test_raw_context_parent_is_reserved_and_rejected_before_context_or_role_io(
    control, tmp_path, monkeypatch, case
):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid context must fail before any context or role read")

    monkeypatch.setattr(runner, "verify_campaign_context_bundle", forbidden)
    changes = {
        "raw_context_bundle": tmp_path / "absent-context",
        "raw_context_receipt": unregistered_context_receipt(control),
    }
    reason = "raw_context_parent_mismatch"
    if case.startswith("missing-"):
        changes["raw_context_bundle" if case == "missing-bundle" else "raw_context_receipt"] = None
        reason = "raw_context_pair_required"
    elif case == "overlap":
        changes["raw_context_bundle"] = control["output_root"]
        reason = "output_overlaps_raw_context"
    with pytest.raises(SnapshotError, match=reason):
        execute(control, **changes)
    assert not {"prepare", "predict", "consume", "finalize"} & set(control["calls"])
    completion = journal.inspect(control["root"]).events[-1]
    assert completion.kind == "finished" and completion.result == "failed"
    assert completion.cost.wall_seconds > 0


def test_reserved_all_trial_evaluation_is_durable_before_finish_and_readonly_verification(
    control, monkeypatch
):
    original = journal.finish

    def finish(root, reservation, **kwargs):
        if kwargs["result"] == "completed":
            assert (root / "receipts" / (reservation + ".json")).is_file()
        return original(root, reservation, **kwargs)

    monkeypatch.setattr(journal, "finish", finish)
    bundle, receipt = execute(control)
    assert control["calls"][-6:] == [
        "prepare",
        "predict",
        "consume",
        "predict",
        "consume",
        "finalize",
    ]
    assert receipt.full_role_label_file_passes == 1 and receipt.actual_index_passes == 3
    assert receipt.all_frozen_trials_compared and not receipt.final_test_accessed
    assert not receipt.stage_ready and not receipt.quality_qualified
    assert set(receipt.trial_prediction_trace_sha256) == set(
        control["recipe"].tune_score_operation_ids
    )
    event = journal.inspect(control["root"]).events[-1]
    assert event.result == "completed" and event.evidence_sha256 == receipt.content_sha256()
    assert (
        event.cost.peak_process_tree_rss_bytes == 12345
        and event.cost.artifact_bytes == receipt.artifact_bytes
    )
    assert not list(bundle.parent.glob("trial-*/predictions.sqlite"))
    original_bytes = (control["root"] / "journal.json").read_bytes()
    runner.verify_campaign_forecast_evaluation(bundle, journal=control["root"], receipt=receipt)
    assert (control["root"] / "journal.json").read_bytes() == original_bytes
    with pytest.raises(ValidationError, match="budget_exhausted"):
        execute(control)
    (bundle / "metrics.json").write_bytes(b"{}\n")
    with pytest.raises(SnapshotError, match="checksum"):
        runner.verify_campaign_forecast_evaluation(bundle, journal=control["root"], receipt=receipt)


@pytest.mark.parametrize("phase", ["prepare", "predict", "consume", "finalize"])
def test_failed_phase_keeps_reservation_cost_and_budget_without_retry(control, monkeypatch, phase):
    original = runner.monitor

    def fail(command, **kwargs):
        if command[-2] == phase:
            return {
                "status": "failed",
                "reason": "declared-controlled-failure",
                "sampled_tree_peak_rss_bytes": 45678,
            }
        return original(command, **kwargs)

    monkeypatch.setattr(runner, "monitor", fail)
    with pytest.raises(SnapshotError, match="phase_failed_" + phase):
        execute(control)
    event = journal.inspect(control["root"]).events[-1]
    assert event.result == "failed" and event.cost.wall_seconds > 0
    assert event.cost.peak_process_tree_rss_bytes == 45678
    with pytest.raises(ValidationError, match="budget_exhausted"):
        execute(control)


@pytest.mark.parametrize("mutation", ["recipe", "configuration", "fit", "parent-receipt"])
def test_wrong_recipe_or_parent_fails_after_reservation_before_role_io(
    control, monkeypatch, mutation
):
    changes = {}
    if mutation == "recipe":
        changes["recipe"] = control["recipe"].model_copy(update={"max_rows": 1})
    elif mutation == "configuration":
        changes["configuration"] = control["configuration"].model_copy(
            update={"feature_schema_sha256": "f" * 64}
        )
    elif mutation == "fit":
        fits = copy.deepcopy(control["fits"])
        key = next(iter(fits))
        fits[key] = fits[key].model_copy(update={"encoding_sha256": "f" * 64})
        changes["fits"] = fits
    else:
        path = control["root"] / "receipts" / (control["calibration"].reservation_id + ".json")
        path.write_bytes(b"{}\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid recipe or parent must fail before role preparation")

    monkeypatch.setattr(worker, "prepare", forbidden)
    with pytest.raises(SnapshotError):
        execute(control, **changes)
    assert journal.inspect(control["root"]).events[-1].result == "failed"


def test_result_receipt_flags_cannot_relabel_partial_robustness_as_qualified(control):
    bundle, receipt = execute(control)
    changed = receipt.model_copy(update={"quality_qualified": True})
    with pytest.raises(ValidationError):
        type(changed).model_validate_json(canonical_bytes(changed.model_dump(mode="json")))
    changed = receipt.model_copy(
        update={
            "critical_segment_inventory_complete": True,
            "block_uncertainty_complete": True,
            "quality_qualified": True,
        }
    )
    with pytest.raises(SnapshotError, match="scope_or_qualification"):
        runner._verify_bundle(bundle, changed)


def test_receipt_publication_failure_keeps_known_artifact_and_phase_cost(control, monkeypatch):
    def interrupted(*args):
        raise OSError("declared-receipt-publication-failure")

    monkeypatch.setattr(runner, "_store_receipt", interrupted)
    with pytest.raises(OSError, match="publication-failure"):
        execute(control)
    event = journal.inspect(control["root"]).events[-1]
    assert event.result == "failed" and event.cost.artifact_bytes > 0
    assert event.cost.peak_process_tree_rss_bytes == 12345
    assert event.cost.wall_seconds > 0
    assert not (control["root"] / "receipts" / (event.reservation_id + ".json")).exists()
    with pytest.raises(ValidationError, match="budget_exhausted"):
        execute(control)


def test_worker_reported_peak_is_charged_even_if_monitor_sample_is_lower(control, monkeypatch):
    original = runner.monitor
    peak = control["recipe"].resources.tree_rss_bytes + 1

    def oversized(command, **kwargs):
        result = original(command, **kwargs)
        folder = kwargs["root"]
        path = folder / (command[-2] + ".json")
        report = read(path) | {"worker_peak_rss_bytes": peak}
        path.unlink()
        write(path, report)
        return result

    monkeypatch.setattr(runner, "monitor", oversized)
    with pytest.raises(SnapshotError, match="worker_peak_rss_limit"):
        execute(control)
    event = journal.inspect(control["root"]).events[-1]
    assert event.result == "failed" and event.cost.peak_process_tree_rss_bytes == peak


def test_metadata_only_three_use_freeze_cannot_authorize_final_evaluation(control):
    selection = declared_selection(control["root"])
    journal.freeze_selection(control["root"], selection)
    ledger = journal.inspect(control["root"])
    with pytest.raises(SnapshotError, match="no_completed_use_evaluation"):
        runner._selection(
            control["root"],
            ledger,
            SimpleNamespace(selection_sha256=canonical_sha256(selection.model_dump(mode="json"))),
            control["configuration"],
            control["calibration"],
            {use: control["output_root"] for use in ("forecast", "anomaly", "stockout")},
        )


def test_completed_forecast_component_without_full_robustness_still_blocks_final(control):
    bundle, receipt = execute(control)
    selection = declared_selection(control["root"])
    fields = selection.model_dump(mode="json")
    fields["bundles"][0] |= receipt.selection_components.model_dump(mode="json") | {
        "selection_evidence_sha256": receipt.content_sha256(),
    }
    selection = type(selection).model_validate_json(canonical_bytes(fields))
    journal.freeze_selection(control["root"], selection)
    ledger = journal.inspect(control["root"])
    with pytest.raises(SnapshotError, match="receipt_incomplete"):
        runner._selection(
            control["root"],
            ledger,
            SimpleNamespace(selection_sha256=canonical_sha256(selection.model_dump(mode="json"))),
            control["configuration"],
            control["calibration"],
            {use: bundle for use in ("forecast", "anomaly", "stockout")},
        )


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing", "receipt_unavailable"),
        ("malformed", "receipt_invalid"),
        ("permissions", "private_selection_receipt_required"),
    ],
)
def test_completed_selection_rejects_unavailable_or_invalid_private_receipt_without_role_io(
    control, change, reason
):
    bundle, receipt = execute(control)
    selection = declared_selection(control["root"])
    fields = selection.model_dump(mode="json")
    fields["bundles"][0] |= receipt.selection_components.model_dump(mode="json") | {
        "selection_evidence_sha256": receipt.content_sha256(),
    }
    selection = type(selection).model_validate_json(canonical_bytes(fields))
    journal.freeze_selection(control["root"], selection)
    stored = control["root"] / "receipts" / (receipt.reservation_id + ".json")
    if change == "missing":
        stored.unlink()
    elif change == "malformed":
        stored.write_bytes(b"{invalid-json\n")
    else:
        stored.chmod(0o644)
    before = journal.inspect(control["root"]).head_sha256
    calls = list(control["calls"])
    with pytest.raises(SnapshotError, match=reason):
        runner._selection(
            control["root"],
            journal.inspect(control["root"]),
            SimpleNamespace(selection_sha256=canonical_sha256(selection.model_dump(mode="json"))),
            control["configuration"],
            control["calibration"],
            {use: bundle for use in ("forecast", "anomaly", "stockout")},
        )
    assert journal.inspect(control["root"]).head_sha256 == before
    assert control["calls"] == calls
