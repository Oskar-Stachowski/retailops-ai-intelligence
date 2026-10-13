"""Complete controller path with declared Source/model/calibration workers.

Small exposed role bodies feed the real evaluation SQL/metrics/robustness code.
This verifies integration and rejection, never Project data or quality acceptance.
"""

import hashlib
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_campaign_generation as generation_controls
from test_development_advancement import trial_values
from test_development_screening import native_double
from test_development_workflow import program as program  # noqa: F401
from test_forecast_features import SERIES
from test_forecast_features import tables as tables  # noqa: F401
from test_forecast_manifests import timeline as timeline  # noqa: F401
from test_independent_forecast_partitions import population as population  # noqa: F401
from test_physical_forecast import stored_control as stored_control  # noqa: F401
from test_source_preparation import setup as source_setup  # noqa: F401

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_calibration as calibration
from retailops_ai.evaluation_campaign import campaign_context_bundle as context_api
from retailops_ai.evaluation_campaign import campaign_evaluation as evaluation
from retailops_ai.evaluation_campaign import campaign_evaluation_data as data
from retailops_ai.evaluation_campaign import campaign_evaluation_worker as worker
from retailops_ai.evaluation_campaign import campaign_export as exporter
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import development_execution as forecast
from retailops_ai.evaluation_campaign import physical_forecast as physical
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibrationPlan,
)
from retailops_ai.evaluation_campaign.campaign_calibration_data import fit_horizons
from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
    CampaignContextBundleRecipe,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_metadata import read
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
    CampaignGeneratedParentReceipt,
)
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.evaluation_campaign.campaign_robust_receipt import (
    CanonicalQualificationEvaluationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScoreReceipt
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastContextScope,
    CampaignSourceKeyAnnotation,
)
from retailops_ai.evaluation_campaign.campaign_segments import SegmentCensus, context_from_inputs
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.evaluation_campaign.canonical_qualification import (
    PreparedCanonicalQualificationProtocol,
)
from retailops_ai.evaluation_campaign.canonical_qualification_execution_contract import (
    CanonicalQualificationExecutionResult,
)
from retailops_ai.evaluation_campaign.development_workflow import run_development_workflow
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastExample,
    PhysicalForecastManifest,
)
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.features import OriginFeatures


def declared_distinct_sources(monkeypatch):
    """Give each declared generation its own lineage before its receipt is written."""
    original = generation_controls.fake_phases
    source = generation_controls.declared_source

    def phases(patch, case, **kwargs):
        spec = source()
        digest = canonical_sha256({"declared_generation": case[3].source_recipe_sha256})
        parent = spec.parent.model_copy(
            update={
                "source_dataset_id": "source-sha256-" + digest,
                "snapshot_id": "snapshot-sha256-" + digest,
                "curated_dataset_id": "curated-sha256-" + digest,
                "curated_descriptor_sha256": digest,
            }
        )
        with monkeypatch.context() as temporary:
            temporary.setattr(
                generation_controls,
                "declared_source",
                lambda: spec.model_copy(update={"parent": parent}),
            )
            return original(patch, case, **kwargs)

    monkeypatch.setattr(generation_controls, "fake_phases", phases)


def declared_exports(monkeypatch, program, stored_control, population, timeline):
    root, _, policies, state = program
    template, manifest = stored_control
    category = policies.policies.required_groups.segment_policy.category_inventory[0]
    rows = tuple(
        row.model_copy(
            update={
                "values": tuple(
                    item.model_copy(update={"value": category})
                    if item.name == "category_id"
                    else item
                    for item in row.values
                )
            }
        )
        for row in population[1]
    )
    row_hash = {membership_key(row): canonical_sha256(row.model_dump(mode="json")) for row in rows}
    histories = {
        row.history_context_sha256: OriginFeatures(
            timeline, make_origin(row.forecast_origin.date())
        ).history(SERIES)
        for row in rows
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
            feature_set_id=read(path / "feature_manifest.json")["feature_set_id"],
            descriptor=manifest.descriptor.feature_descriptor.model_validate_json(
                canonical_bytes(read(path / "feature_manifest.json")["descriptor"])
            ),
        ),
    )
    original = forecast.export_development_forecast

    def export(snapshot, curated, output, **kwargs):
        protocol = journal.inspect(kwargs["journal"]).protocol
        if not isinstance(protocol, PreparedCanonicalQualificationProtocol):
            return original(snapshot, curated, output, **kwargs)
        prefix = protocol.qualification.prefix
        index = prefix.exports.index(kwargs["plan"])
        native_double(monkeypatch, (kwargs["journal"], prefix, output), index)

        def build(parent, recipe, destination):
            feature = manifest.descriptor.feature_descriptor.model_copy(
                update={
                    "parent": recipe.source.parent,
                    "source_parameters": recipe.source.source_parameters,
                    "resolved_policy": recipe.features,
                }
            )
            populations, bodies = {}, {}
            for role in physical.ALL_ROLES:
                examples = []
                for line in (template / (role + ".jsonl")).read_bytes().splitlines():
                    item = PhysicalForecastExample.model_validate_json(line)
                    digest = row_hash[membership_key(item.membership)]
                    item = item.model_copy(
                        update={
                            "membership": item.membership.model_copy(
                                update={"feature_row_sha256": digest}
                            ),
                            "outcome": item.outcome.model_copy(
                                update={"feature_row_sha256": digest}
                            )
                            if item.outcome
                            else None,
                        }
                    )
                    examples.append(item)
                bodies[role] = b"".join(
                    canonical_bytes(e.model_dump(mode="json")) + b"\n" for e in examples
                )
                counts = Counter()
                for example in examples:
                    physical._counts(counts, example)
                keys = b"".join(membership_key(e.membership) + b"\n" for e in examples)
                populations[role] = physical._role_file(
                    counts,
                    len(bodies[role]),
                    hashlib.sha256(bodies[role]).hexdigest(),
                    hashlib.sha256(keys).hexdigest(),
                )
            descriptor = manifest.descriptor.model_copy(
                update={
                    "recipe": recipe,
                    "runtime": protocol.runtime,
                    "feature_descriptor": feature,
                    "feature_set_id": "features-sha256-"
                    + canonical_sha256(feature.model_dump(mode="json")),
                    "populations": populations,
                    "snapshot_inventory_sha256": parent.snapshot_inventory_sha256,
                    "curated_inventory_sha256": parent.curated_inventory_sha256,
                    "logical_curated_sha256": parent.logical_curated_sha256,
                }
            )
            exported_manifest = PhysicalForecastManifest(
                dataset_id="ai09-physical-forecast-sha256-"
                + canonical_sha256(descriptor.model_dump(mode="json")),
                descriptor=descriptor,
            )
            path = destination / exported_manifest.dataset_id
            path.mkdir(mode=0o700)
            (path / "features").mkdir(mode=0o700)
            for role, raw in bodies.items():
                (path / (role + ".jsonl")).write_bytes(raw)
            write(path / "manifest.json", exported_manifest.model_dump(mode="json"))
            write(
                path / "features/feature_manifest.json",
                {
                    "feature_set_id": descriptor.feature_set_id,
                    "descriptor": feature.model_dump(mode="json"),
                },
            )
            return path

        monkeypatch.setattr(exporter, "_build_physical_forecast", build)
        monkeypatch.setattr(
            exporter,
            "verify_physical_forecast",
            lambda path: PhysicalForecastManifest.model_validate_json(
                (path / "manifest.json").read_bytes()
            ),
        )
        state["forecast"].append(kwargs["operation_id"])
        return exporter.export_development_forecast(snapshot, curated, output, **kwargs)

    monkeypatch.setattr(forecast, "export_development_forecast", export)


def declared_calibration(monkeypatch, calls):
    def monitor(command, **kwargs):
        calls.append("calibration")
        folder = kwargs["root"]
        request = read(folder / "request.json")
        plan = CampaignForecastCalibrationPlan.model_validate_json(canonical_bytes(request["plan"]))
        tune = CampaignForecastTuneReceipt.model_validate_json(canonical_bytes(request["tune"]))
        score = CampaignForecastScoreReceipt.model_validate_json(
            canonical_bytes(request["scores"][plan.score_operation_ids[0]])
        )
        with closing(sqlite3.connect(folder / "declared-residuals.sqlite")) as db:
            db.execute("CREATE TABLE residuals(horizon INTEGER,error REAL,ordinal INTEGER)")
            db.executemany(
                "INSERT INTO residuals VALUES (?,0,?)",
                ((h, i) for h in range(1, 15) for i in range(100)),
            )
            calibrated = fit_horizons(
                db, {h: (100, 100) for h in range(1, 15)}, tune.selection, score.operation_id, plan
            )
        assert calibrated.calibration_fitted
        population = {k: getattr(score, k) for k in calibration.POPULATION} | {
            "selected_median_residuals_sha256": "a" * 64,
            "selected_prediction_file_sha256": score.artifact_files["predictions.jsonl"],
            "full_calibration_label_passes": 1,
            "tune_label_passes": 0,
            "independent_or_final_label_passes": 0,
            "architecture_reselected": False,
        }
        bundle = folder / "bundle"
        bundle.mkdir(mode=0o700)
        write(bundle / "plan.json", plan.model_dump(mode="json"))
        write(
            bundle / "parents.json",
            {k: request[k] for k in ("exported", "tune", "tune_scores", "scores")},
        )
        write(bundle / "population.json", population)
        write(bundle / "calibration.json", calibrated.model_dump(mode="json"))
        write(
            folder / "calibrate.json",
            population
            | {"calibration": calibrated.model_dump(mode="json"), "worker_peak_rss_bytes": 12345},
        )
        return {"status": "passed", "reason": None, "sampled_tree_peak_rss_bytes": 12345}

    monkeypatch.setattr(calibration, "monitor", monitor)


def declared_contexts(monkeypatch, calls):
    def monitor(command, **kwargs):
        folder = kwargs["root"]
        request = read(folder / "request.json")
        calls.append("context")
        recipe = CampaignContextBundleRecipe.model_validate_json(canonical_bytes(request["recipe"]))
        exported = CampaignDevelopmentExportReceipt.model_validate_json(
            canonical_bytes(request["exported"])
        )
        generated = CampaignGeneratedParentReceipt.model_validate_json(
            canonical_bytes(request["generated"])
        )
        dataset = Path(request["dataset"])
        manifest = PhysicalForecastManifest.model_validate_json(
            (dataset / "manifest.json").read_bytes()
        )
        scope = CampaignForecastContextScope(
            data_seed=42,
            role=recipe.role,
            dataset_id=exported.dataset_id,
            source_recipe_sha256=recipe.source_recipe_sha256,
            source_dataset_id=generated.source.parent.source_dataset_id,
            snapshot_id=generated.source.parent.snapshot_id,
            curated_dataset_id=generated.source.parent.curated_dataset_id,
            source_scenario_plan_sha256=request["source_recipe"].get("scenario_plan_sha256"),
            segment_policy_sha256=recipe.segment_policy.content_sha256(),
        )
        bundle = folder / "bundle"
        bundle.mkdir(mode=0o700)
        census = SegmentCensus(scope, recipe.segment_policy)
        with (
            closing(physical._index(folder / "inputs.sqlite", recipe.max_index_bytes)) as inputs,
            closing(physical._index(folder / "actuals.sqlite", recipe.max_index_bytes)) as actuals,
        ):
            population = data.index_role(inputs, actuals, dataset, manifest, recipe)
            with (bundle / "contexts.jsonl").open("wb") as stream:
                for window in data.windows(inputs, recipe):
                    for record in window.records:
                        annotation = CampaignSourceKeyAnnotation(
                            **record.row.model_dump(include=set(ForecastKey.model_fields)),
                            context_scope_sha256=scope.content_sha256(),
                            source_scenario_plan_sha256=scope.source_scenario_plan_sha256,
                            scenario="normal",
                            anomaly="unannotated",
                        )
                        context = context_from_inputs(
                            record.row,
                            window.history,
                            None,
                            None,
                            annotation,
                            scope=scope,
                            policy=recipe.segment_policy,
                            example_sha256=record.example_sha256,
                            eligible=record.eligible,
                            exclusion_reasons=record.exclusion_reasons,
                        )
                        census.add(context)
                        stream.write(canonical_bytes(context.model_dump(mode="json")) + b"\n")
        complete = census.finish(
            **{
                "expected_" + k: population[k]
                for k in ("rows", "eligible_rows", "keys_sha256", "eligible_keys_sha256")
            }
        )
        seal = {
            k: getattr(exported, k)
            for k in (
                "snapshot_inventory_sha256",
                "curated_inventory_sha256",
                "logical_curated_sha256",
            )
        }
        write(bundle / "recipe.json", recipe.model_dump(mode="json"))
        write(
            bundle / "parents.json",
            {k: request[k] for k in ("generation", "generated", "exported")},
        )
        write(bundle / "scope.json", scope.model_dump(mode="json"))
        write(bundle / "population.json", population)
        write(bundle / "census.json", complete.model_dump(mode="json"))
        write(
            bundle / "source-seal.json",
            seal | {"source_scenario_plan_sha256": scope.source_scenario_plan_sha256},
        )
        hashes, size = _bundle_inventory(bundle, recipe.max_output_bytes)
        write(
            folder / "result.json",
            {
                "scope": scope.model_dump(mode="json"),
                "population": population,
                "context_trace_sha256": complete.context_trace_sha256,
                "census_sha256": canonical_sha256(complete.model_dump(mode="json")),
                **seal,
                "artifact_files": hashes,
                "artifact_sha256": canonical_sha256(hashes),
                "artifact_bytes": size,
                "full_role_label_file_passes": 2,
                "complete_export_role_file_passes": 6,
                "worker_peak_rss_bytes": 12345,
            },
        )
        return {"status": "passed", "reason": None, "sampled_tree_peak_rss_bytes": 12345}

    monkeypatch.setattr(context_api, "monitor", monitor)


def native_evaluation_with_declared_models(monkeypatch, calls):
    # Public fit verification retains the original fixture receipts. Numerical
    # evaluation/indexing and complete robustness/report verification stay real.
    monkeypatch.setattr(
        evaluation, "verify_campaign_forecast_bundle", forecast.verify_campaign_forecast_bundle
    )
    monkeypatch.setattr(worker, "load_models", lambda request: ({}, {}))
    monkeypatch.setattr(
        worker,
        "infer_functionals",
        lambda windows, encodings, models: {
            record.key: trial_values("declared-evaluation")
            if record.eligible
            else tuple(
                type(v)(mean=None, median=None, interval=None)
                for v in trial_values("declared-evaluation")
            )
            for window in windows
            for record in window.records
        },
    )

    def monitor(command, **kwargs):
        phase, folder = command[-2], kwargs["root"]
        calls.append("evaluate-" + phase)
        request = read(folder / "request.json")
        plan = CampaignForecastEvaluationPlan.model_validate_json(canonical_bytes(request["plan"]))
        result = getattr(worker, phase)(folder, request, plan)
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

    monkeypatch.setattr(evaluation, "monitor", monitor)


@pytest.mark.parametrize("program", ["qualified"], indirect=True)
def test_all_51_original_operations_complete_all_variants_and_resume_without_workers(
    program, stored_control, population, timeline, monkeypatch
):
    root, options, policies, state = program
    state["calibration_horizons"] = 14
    calls = []
    declared_distinct_sources(monkeypatch)
    declared_exports(monkeypatch, program, stored_control, population, timeline)
    declared_calibration(monkeypatch, calls)
    declared_contexts(monkeypatch, calls)
    native_evaluation_with_declared_models(monkeypatch, calls)
    partial = run_development_workflow(root, options, policies, maximum_new_operations=47)
    assert partial["status"] == "incomplete"
    assert partial["planned_operations"] == 51
    assert partial["completed_operations"] == partial["new_operations"] == 47
    assert calls.count("calibration") == calls.count("context") == 1
    assert calls.count("evaluate-finalize") == 1
    result = run_development_workflow(root, options, policies)
    assert result["planned_operations"] == result["completed_operations"] == 51
    assert result["new_operations"] == 4
    assert result["status"] == "completed" and not result["stage_ready"]
    assert not result["quality_qualified"] and not result["development_quality_qualified"]
    evidence = CanonicalQualificationExecutionResult.model_validate_json(
        (root / "100/forecast/qualification-execution-result.json").read_bytes()
    )
    assert len(evidence.operation_costs) == 16 and set(evidence.evaluation_quality) == {
        "ordinary",
        "demand",
        "physical",
    }
    ledger = journal.inspect(root / "100/forecast")
    assert len(ledger.events) == 32
    for index in (11, 13, 15):
        event = ledger.events[2 * index + 1]
        path = root / "100/forecast/receipts" / (event.reservation_id + ".json")
        assert path.stat().st_size > 4 * 1024**2
        receipt = CanonicalQualificationEvaluationReceipt.model_validate_json(path.read_bytes())
        assert receipt.portfolio_protocol == evidence.protocol
        assert (
            receipt.plan.source_recipe_sha256
            == evidence.protocol.sources[(index - 11) // 2].content_sha256()
        )
        assert receipt.critical_segment_inventory_complete and receipt.block_uncertainty_complete
        assert not receipt.quality_qualified
    assert calls.count("calibration") == 1 and calls.count("context") == 3
    assert calls.count("evaluate-prepare") == calls.count("evaluate-finalize") == 3
    all_ledgers = [
        journal.inspect(root / str(scale) / name)
        for scale in (25, 50, 100)
        for name in ("source/planning", "source/variants", "forecast")
    ]
    assert sum(len(item.events) for item in all_ledgers) == 102
    assert evidence.cold_wall_seconds() == pytest.approx(
        sum(
            event.cost.wall_seconds
            for item in all_ledgers
            for event in item.events
            if event.kind == "finished"
        )
    )
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.json")}
    before_calls = (list(calls), list(state["calls"]), list(state["forecast"]))
    resumed = run_development_workflow(root, options, policies)
    assert resumed["new_operations"] == 0 and resumed["completed_operations"] == 51
    assert before == {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*.json")
    }
    assert before_calls == (calls, state["calls"], state["forecast"])
