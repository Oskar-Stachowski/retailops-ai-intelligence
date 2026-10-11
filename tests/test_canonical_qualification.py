"""Compile against real original journals with explicitly declared Source/model doubles."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_development_workflow import program as program  # noqa: F401
from test_source_preparation import setup as source_setup  # noqa: F401

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_calibration as calibration_runner
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibrationPlan,
)
from retailops_ai.evaluation_campaign.campaign_calibration_data import fit_horizons
from retailops_ai.evaluation_campaign.campaign_evaluation_worker import read as read_evaluation
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    CampaignPortfolioProtocol,
    parse_campaign_journal,
    parse_portfolio_protocol,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_evaluation_contract import (
    CanonicalQualificationEvaluationBinding,
    parse_portfolio_evaluation_binding,
)
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScoreReceipt
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.evaluation_campaign.canonical_development_contract import (
    PreparedCanonicalDevelopmentParents,
)
from retailops_ai.evaluation_campaign.canonical_qualification import (
    CanonicalQualificationPlan,
    PreparedCanonicalQualificationJournal,
    compile_canonical_qualification,
    qualification_protocol,
)
from retailops_ai.evaluation_campaign.canonical_qualification_execution_contract import (
    CanonicalQualificationExecutionResult,
)
from retailops_ai.evaluation_campaign.development_advancement_contract import (
    CompletedForecastAdvancement,
)
from retailops_ai.evaluation_campaign.development_execution import (
    DevelopmentExecutionPaths,
    DevelopmentParentPaths,
    _protocol,
    run_development_forecast,
)
from retailops_ai.evaluation_campaign.development_workflow import (
    _forecast,
    run_development_workflow,
)
from retailops_ai.evaluation_campaign.development_workflow_contract import (
    DevelopmentWorkflowOptions,
    QualifiedDevelopmentWorkflowOptions,
    parse_development_workflow_options,
)
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.mark.parametrize("program", ["qualified"], indirect=True)
def test_full_qualification_plan_preserves_original_search_and_freezes_all_variants(
    program, monkeypatch
):
    root, options, policies, state = program
    result = run_development_workflow(root, options, policies, maximum_new_operations=35)
    assert result["completed_operations"] == 35 and result["new_operations"] == 35
    assert result["planned_operations"] == 51
    assert isinstance(options, QualifiedDevelopmentWorkflowOptions)
    assert parse_development_workflow_options(options.model_dump_json()) == options
    frozen_program = json.loads((root / "development-workflow.json").read_bytes())
    assert frozen_program["options"] == options.model_dump(mode="json")
    assert frozen_program["version"] == "ai09-qualified-development-workflow-1.0.0"
    for field, value in (
        ("maximum_fit_attempts", 13),
        ("maximum_calibrator_fit_attempts", 2),
        ("maximum_forecast_operations", 35),
    ):
        with pytest.raises(ValidationError):
            parse_development_workflow_options(
                canonical_bytes(options.model_dump(mode="json") | {field: value})
            )
    with pytest.raises(ValidationError):
        DevelopmentWorkflowOptions.model_validate_json(options.model_dump_json())
    assert not (root / "100/forecast").exists()
    advancement = CompletedForecastAdvancement.model_validate_json(
        (root / "50/forecast/forecast-execution-result.json").read_bytes()
    )
    parent_result = json.loads((root / "100/source/source-preparation-result.json").read_bytes())
    parents = PreparedCanonicalDevelopmentParents.model_validate_json(
        canonical_bytes(parent_result["parents"])
    )
    before = {str(p): p.read_bytes() for p in root.rglob("*.json")}
    calls = {k: list(state[k]) for k in ("calls", "forecast")}
    plan = compile_canonical_qualification(
        advancement=advancement, parents=parents, policies=policies
    )
    assert CanonicalQualificationPlan.model_validate_json(plan.model_dump_json()) == plan
    assert (
        CanonicalQualificationPlan.model_validate_json(
            canonical_bytes(plan.model_dump(mode="json"))
        )
        == plan
    )
    assert plan.operations[:8] == plan.prefix.operations
    assert plan.prefix.advancement == advancement and plan.prefix.parents == parents
    assert plan.maximum_model_fit_attempts_all_scales == 12
    assert sum(o.action == "model_fit" for o in plan.operations) == 3
    assert sum(o.action == "calibrator_fit" for o in plan.operations) == 1
    assert len(plan.operations) == plan.maximum_new_attempts == 16
    budget = advancement.protocol.screening.protocol.future_budgets["100"]
    assert plan.calibration_score.resources == plan.calibration.resources == budget.score
    for i, variant in enumerate(("ordinary", "demand", "physical")):
        context, evaluation = plan.contexts[i], plan.evaluations[i]
        assert context.resources == budget.preparation and evaluation.resources == budget.score
        assert context.generation_operation_id == parents.generations[i].operation_id
        assert context.source_recipe_sha256 == parents.preparation.sources[i].content_sha256()
        assert evaluation.source_recipe_sha256 == context.source_recipe_sha256
        assert context.segment_policy == policies.policies.required_groups.segment_policy
        assert evaluation.quality_policy == policies.policies.forecast_quality
        assert (
            evaluation.uncertainty_policy_sha256 == policies.policies.uncertainty.content_sha256()
        )
        assert evaluation.tune_score_operation_ids == plan.prefix.selection_plan.score_operation_ids
        assert plan.operations[10 + i * 2].operation_id.endswith(variant + "-context")
        assert plan.operations[11 + i * 2].operation_id.endswith(variant + "-evaluate")
        assert "development-100-calibrate" in plan.operations[10 + i * 2].prerequisites
    assert not any(
        (
            plan.authorizes_operations,
            plan.final_test_access_authorized,
            plan.quality_qualified,
            plan.stage_ready,
        )
    )
    assert before == {str(p): p.read_bytes() for p in root.rglob("*.json")}
    assert calls == {k: state[k] for k in calls}

    for attack in ("trial", "budget", "variant", "policy", "order", "missing", "grant"):
        raw = plan.model_dump(mode="json")
        if attack == "trial":
            raw["calibration_score"]["fit_operation_ids"]["tensorflow"] += "-new-trial"
        elif attack == "budget":
            raw["evaluations"][2]["resources"]["wall_seconds"] += 1
        elif attack == "variant":
            raw["evaluations"][2]["source_recipe_sha256"] = raw["evaluations"][0][
                "source_recipe_sha256"
            ]
        elif attack == "policy":
            raw["contexts"][1]["segment_policy"]["late_observation_hours"] += 1
        elif attack == "order":
            raw["operations"][8]["prerequisites"].remove(plan.prefix.operations[7].operation_id)
        elif attack == "missing":
            raw["operations"].pop()
        else:
            raw["authorizes_operations"] = True
        with pytest.raises(ValidationError):
            CanonicalQualificationPlan.model_validate_json(canonical_bytes(raw))
    assert before == {str(p): p.read_bytes() for p in root.rglob("*.json")}

    protocol = qualification_protocol(plan)
    assert parse_portfolio_protocol(protocol.model_dump_json().encode()) == protocol
    assert parse_portfolio_protocol(canonical_bytes(protocol.model_dump(mode="json"))) == protocol
    with pytest.raises(ValidationError):
        CampaignPortfolioProtocol.model_validate_json(protocol.model_dump_json())
    policies.policies.required_groups.bind(protocol)
    binding = CanonicalQualificationEvaluationBinding(
        protocol=protocol,
        operation_id=protocol.operations[-1].operation_id,
        recipe=plan.evaluations[-1],
        training_dataset_id="ai09-physical-forecast-sha256-" + "0" * 64,
        evaluation_dataset_id="ai09-physical-forecast-sha256-" + "1" * 64,
    )
    assert parse_portfolio_evaluation_binding(binding.model_dump_json().encode()) == binding
    request = root.parent / "full-lineage-evaluation-request.json"
    request.write_bytes(
        canonical_bytes(
            {
                "portfolio_binding": binding.model_dump(mode="json"),
                "portfolio_protocol": protocol.model_dump(mode="json"),
            }
        )
    )
    assert request.stat().st_size > 4 * 1024**2
    decoded = read_evaluation(request)
    assert parse_portfolio_protocol(canonical_bytes(decoded["portfolio_protocol"])) == protocol
    assert (
        parse_portfolio_evaluation_binding(canonical_bytes(decoded["portfolio_binding"])) == binding
    )
    with pytest.raises(ValueError, match="campaign_generation_invalid_metadata"):
        read(request)

    # The driver admits the frozen full journal; an eight-operation declaration cannot replace it.
    path = Path(plan.prefix.journal_path)
    initialized = _forecast(root, options, 100, parents, advancement, policies=policies)
    assert initialized == protocol
    original = journal.inspect(path)
    assert isinstance(original, PreparedCanonicalQualificationJournal)
    assert parse_campaign_journal(original.model_dump_json().encode()) == original
    assert _protocol(original) == protocol
    original_bytes = (path / "journal.json").read_bytes()
    with pytest.raises(SnapshotError, match="already_frozen"):
        journal.initialize(path, plan.prefix)
    with pytest.raises(ValidationError):
        journal.reserve(path, protocol.operations[8].operation_id)
    with pytest.raises(SnapshotError, match="path_already_used"):
        compile_canonical_qualification(advancement=advancement, parents=parents, policies=policies)
    assert (path / "journal.json").read_bytes() == original_bytes
    assert not journal.inspect(path).events

    output = root / "100/forecast-artifacts"
    output.mkdir(mode=0o700)
    paths = DevelopmentExecutionPaths(
        protocol_sha256=protocol.content_sha256(),
        parents=tuple(
            DevelopmentParentPaths(
                **{k: parent_result["parent_paths"][v][k] for k in ("snapshot", "curated")}
            )
            for v in ("ordinary", "demand", "physical")
        ),
        worker_python=options.worker_python,
        output_root=str(output),
    )
    selected = run_development_forecast(path, paths, maximum_new_operations=8)
    assert selected["completed_operations"] == 8 and selected["status"] == "incomplete"
    scored = run_development_forecast(path, paths, maximum_new_operations=1)
    assert scored["completed_operations"] == 9 and scored["new_operations"] == 1
    assert not (path / "forecast-execution-result.json").exists()
    assert not (path / "qualification-execution-result.json").exists()
    worker_calls = []

    def calibration_worker(command, **kwargs):
        # Explicitly declared native worker double: 100 rows at horizon1 only.
        # The real controller and fit_horizons must retain not_ready for horizons2-14.
        worker_calls.append(command)
        assert journal.inspect(path).events[-1].operation_id == "development-100-calibrate"
        folder = kwargs["root"]
        request = read(folder / "request.json")
        frozen = CampaignForecastCalibrationPlan.model_validate_json(
            canonical_bytes(request["plan"])
        )
        tune = CampaignForecastTuneReceipt.model_validate_json(canonical_bytes(request["tune"]))
        score = CampaignForecastScoreReceipt.model_validate_json(
            canonical_bytes(request["scores"][frozen.score_operation_ids[0]])
        )
        with closing(sqlite3.connect(folder / "declared-residuals.sqlite")) as db:
            db.execute("CREATE TABLE residuals(horizon INTEGER,error REAL,ordinal INTEGER)")
            db.executemany("INSERT INTO residuals VALUES (1,0,?)", ((i,) for i in range(100)))
            calibration = fit_horizons(
                db,
                {h: (100, 100) if h == 1 else (0, 0) for h in range(1, 15)},
                tune.selection,
                score.operation_id,
                frozen,
            )
        population = {k: getattr(score, k) for k in calibration_runner.POPULATION} | {
            "selected_median_residuals_sha256": "a" * 64,
            "selected_prediction_file_sha256": score.artifact_files["predictions.jsonl"],
            "full_calibration_label_passes": 1,
            "tune_label_passes": 0,
            "independent_or_final_label_passes": 0,
            "architecture_reselected": False,
        }
        bundle = folder / "bundle"
        bundle.mkdir(mode=0o700)
        write(bundle / "plan.json", frozen.model_dump(mode="json"))
        write(
            bundle / "parents.json",
            {k: request[k] for k in ("exported", "tune", "tune_scores", "scores")},
        )
        write(bundle / "population.json", population)
        write(bundle / "calibration.json", calibration.model_dump(mode="json"))
        write(
            folder / "calibrate.json",
            population
            | {"calibration": calibration.model_dump(mode="json"), "worker_peak_rss_bytes": 12345},
        )
        return {"status": "passed", "reason": None, "sampled_tree_peak_rss_bytes": 12345}

    monkeypatch.setattr(calibration_runner, "monitor", calibration_worker)
    calibrated = run_development_forecast(path, paths, maximum_new_operations=1)
    assert calibrated["completed_operations"] == 10 and calibrated["new_operations"] == 1
    assert calibrated["status"] == "not_ready" and calibrated["reasons"] == [
        "calibration_not_ready"
    ]
    assert not calibrated["stage_ready"] and not calibrated["development_quality_qualified"]
    evidence = CanonicalQualificationExecutionResult.model_validate_json(
        (path / "qualification-execution-result.json").read_bytes()
    )
    assert evidence.protocol == protocol and len(evidence.operation_costs) == 10
    assert evidence.calibration_status == "not_ready" and not evidence.evaluation_quality
    assert evidence.cold_wall_seconds() == pytest.approx(
        advancement.cold_wall_seconds()
        + parents.cold_wall_seconds()
        + sum(e.cost.wall_seconds for e in journal.inspect(path).events if e.kind == "finished")
    )
    original_calls = {k: list(state[k]) for k in calls}
    original_files = {str(p): p.read_bytes() for p in root.rglob("*.json")}
    resumed = run_development_forecast(path, paths, maximum_new_operations=16)
    assert resumed["new_operations"] == 0 and resumed["completed_operations"] == 10
    assert resumed["status"] == "not_ready" and len(worker_calls) == 1
    assert original_calls == {k: state[k] for k in calls}
    assert original_files == {str(p): p.read_bytes() for p in root.rglob("*.json")}
    assert not (path / "forecast-execution-result.json").exists()
    whole = run_development_workflow(root, options, policies)
    assert whole["planned_operations"] == 51 and whole["completed_operations"] == 45
    assert whole["new_operations"] == 0 and whole["status"] == "not_ready"
    assert whole["reasons"] == ["calibration_not_ready"]
    assert original_calls == {k: state[k] for k in calls} and len(worker_calls) == 1
    complete = json.loads((root / "development-workflow-result.json").read_bytes())
    assert complete["version"] == "ai09-qualified-development-workflow-result-1.0.0"
    assert complete["canonical_result"] == evidence.model_dump(mode="json")
    downgrade = options.model_dump(
        mode="json", exclude={"maximum_calibrator_fit_attempts", "qualification_scope"}
    )
    downgrade.update(
        version="ai09-original-development-workflow-options-1.0.0", maximum_forecast_operations=28
    )
    original_files = {str(p): p.read_bytes() for p in root.rglob("*.json")}
    with pytest.raises(SnapshotError, match="original_document_changed"):
        run_development_workflow(
            root,
            DevelopmentWorkflowOptions.model_validate_json(canonical_bytes(downgrade)),
            policies,
        )
    assert original_files == {str(p): p.read_bytes() for p in root.rglob("*.json")}
    assert original_calls == {k: state[k] for k in calls} and len(worker_calls) == 1
