"""Original journals/controllers across all scales, with explicitly declared native doubles."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_campaign_score_worker import fake_fits
from test_development_advancement import full_score_phases, paired_selection_worker
from test_development_screening import native_double
from test_source_preparation import (
    setup as source_setup,  # noqa: F401 -- pytest fixture registration
)

from retailops_ai.evaluation_campaign import campaign_export as exporter
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import campaign_score as scorer
from retailops_ai.evaluation_campaign import campaign_tune as tuner
from retailops_ai.evaluation_campaign import development_execution as forecast
from retailops_ai.evaluation_campaign import development_workflow as workflow
from retailops_ai.evaluation_campaign import source_preparation as source
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_fit import validate_completed_fit
from retailops_ai.evaluation_campaign.canonical_forecast_contract import CompletedCanonicalForecast
from retailops_ai.evaluation_campaign.canonical_qualification import (
    PreparedCanonicalQualificationProtocol,
)
from retailops_ai.evaluation_campaign.development_advancement_contract import (
    MultiscaleSelectionProtocol,
)
from retailops_ai.evaluation_campaign.development_search import ForecastSearchRecipe
from retailops_ai.evaluation_campaign.development_workflow_contract import (
    DevelopmentWorkflowOptions,
    ForecastWorkflowResources,
    QualifiedDevelopmentWorkflowOptions,
)
from retailops_ai.evaluation_campaign.preparation_execution import locked
from retailops_ai.forecasting.model_contract import HGBConfig, RFConfig
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.pipeline import environment_lock


@pytest.fixture
def program(source_setup, monkeypatch, request):  # noqa: F811 -- imported pytest fixture
    root, source_options, policies, state = source_setup
    resources = source_options.generation_resources.model_copy(update={"wall_seconds": 1200})
    recipes = (
        *(
            ForecastSearchRecipe(name=f"rf-{i}", family="rf", rf=RFConfig(max_depth=10 + i))
            for i in (1, 2)
        ),
        *(
            ForecastSearchRecipe(name=f"hgb-{i}", family="hgb", hgb=HGBConfig(max_iter=80 * i))
            for i in (1, 2)
        ),
        *(
            ForecastSearchRecipe(
                name=f"tensorflow-{i}", family="tensorflow", learning_rate=0.001 * i
            )
            for i in (1, 2)
        ),
    )
    options_class = (
        QualifiedDevelopmentWorkflowOptions
        if getattr(request, "param", None) == "qualified"
        else DevelopmentWorkflowOptions
    )
    options = options_class(
        source=source_options,
        recipes=recipes,
        worker_python="/declared-model-python",
        worker_environment_lock_sha256=hashlib.sha256(environment_lock()).hexdigest(),
        resources={
            p: ForecastWorkflowResources(fit=resources, score=resources)
            for p in ("25", "50", "100")
        },
    )
    monkeypatch.setattr(
        workflow, "inspect_preparation_producer", source.inspect_preparation_producer
    )
    original_source = workflow.run_source_preparation

    def prepare(*args, **kwargs):
        frozen = json.loads((root / "development-workflow.json").read_bytes())
        assert frozen["options"] == options.model_dump(mode="json")
        return original_source(*args, **kwargs)

    monkeypatch.setattr(workflow, "run_source_preparation", prepare)
    state["forecast"] = []

    def exported(snapshot, curated, output, **kwargs):
        protocol = journal.inspect(kwargs["journal"]).protocol
        prefix = (
            protocol.screening if isinstance(protocol, MultiscaleSelectionProtocol) else protocol
        )
        if isinstance(protocol, PreparedCanonicalQualificationProtocol):
            prefix = protocol.qualification.prefix
        position = next(i for i, p in enumerate(prefix.exports) if p == kwargs["plan"])
        prepared = json.loads(
            (kwargs["journal"].parent / "source/source-preparation-result.json").read_bytes()
        )
        paths = prepared["parent_paths"][("ordinary", "demand", "physical")[position]]
        assert snapshot == Path(paths["snapshot"]) and curated == Path(paths["curated"])
        native_double(monkeypatch, (kwargs["journal"], prefix, output), position)
        state["forecast"].append(kwargs["operation_id"])
        return exporter.export_development_forecast(snapshot, curated, output, **kwargs)

    def fit(dataset, worker_python, output, *, journal: Path, operation_id, plan, exported):
        assert worker_python == Path(options.worker_python)
        start = workflow.campaign_journal.reserve(journal, operation_id)
        receipt = fake_fits(exported.dataset_id)[plan.family].model_copy(
            update={
                "protocol_sha256": exported.protocol_sha256,
                "operation_id": operation_id,
                "reservation_id": str(start.reservation_id),
                "plan": plan,
                "runtime_code_sha256": exported.runtime_code_sha256,
                "export_receipt_sha256": exported.content_sha256(),
            }
        )
        exporter._store_receipt(journal, receipt)
        workflow.campaign_journal.finish(
            journal,
            receipt.reservation_id,
            result="completed",
            evidence_sha256=receipt.content_sha256(),
            cost=CampaignCost(
                wall_seconds=0.01,
                peak_process_tree_rss_bytes=12345,
                artifact_bytes=receipt.model_artifact_bytes,
            ),
        )
        state["forecast"].append(operation_id)
        return output / receipt.reservation_id / "bundle", receipt

    def verify_fit(bundle, *, journal, receipt):
        assert bundle == journal.parent / "forecast-artifacts" / receipt.reservation_id / "bundle"
        validate_completed_fit(journal, receipt)

    def score(dataset, bundles, worker_python, output, **kwargs):
        full_score_phases(
            monkeypatch,
            (
                kwargs["journal"],
                dataset,
                output,
                kwargs["plan"],
                kwargs["exported"],
                kwargs["fits"],
            ),
            calibration_horizons=state.get("calibration_horizons", 1),
        )
        state["forecast"].append(kwargs["operation_id"])
        return scorer.score_campaign_forecast(dataset, bundles, worker_python, output, **kwargs)

    def select(dataset, bundles, worker_python, output, **kwargs):
        paired_selection_worker(
            monkeypatch, kwargs["journal"], kwargs["scores"], kwargs["exported"]
        )
        state["forecast"].append(kwargs["operation_id"])
        return tuner.select_campaign_forecast(dataset, bundles, worker_python, output, **kwargs)

    monkeypatch.setattr(forecast, "export_development_forecast", exported)
    monkeypatch.setattr(forecast, "fit_campaign_forecast", fit)
    monkeypatch.setattr(forecast, "score_campaign_forecast", score)
    monkeypatch.setattr(forecast, "select_campaign_forecast", select)
    monkeypatch.setattr(forecast, "verify_campaign_forecast_bundle", verify_fit)
    monkeypatch.setattr(scorer, "verify_campaign_forecast_bundle", verify_fit)
    return root, options, policies, state


def run(program, limit=43):
    root, options, policies, _ = program
    return workflow.run_development_workflow(root, options, policies, maximum_new_operations=limit)


def test_original_43_operations_all_scales_preserve_every_cost_and_resume_without_workers(program):
    root, options, _, state = program
    assert run(program, 5)["completed_operations"] == 5
    assert not (root / "25/forecast").exists()
    assert run(program, 12)["completed_operations"] == 17
    assert not (root / "50").exists()
    result = run(program)
    assert result["completed_operations"] == 43 and result["new_operations"] == 26
    assert result["status"] == "completed" and not result["stage_ready"]
    assert not result["quality_qualified"] and not result["final_test_access_authorized"]
    assert len(state["forecast"]) == 28
    assert len([c for c in state["forecast"] if "trial" in c and "score" not in c]) == 6
    evidence = json.loads((root / "development-workflow-result.json").read_bytes())
    final = CompletedCanonicalForecast.model_validate_json(json.dumps(evidence["canonical_result"]))
    ledgers = [
        journal.inspect(root / str(p) / name)
        for p in (25, 50, 100)
        for name in ("source/planning", "source/variants", "forecast")
    ]
    assert (
        sum(1 for ledger in ledgers for o in ledger.protocol.operations if o.action == "model_fit")
        == 12
    )
    assert sum(len(ledger.events) for ledger in ledgers) == 86
    assert final.cold_wall_seconds() == pytest.approx(
        sum(
            event.cost.wall_seconds
            for ledger in ledgers
            for event in ledger.events
            if event.kind == "finished"
        )
    )
    assert (
        final.protocol.advancement.protocol.screening.protocol.future_budgets["100"].fit
        == options.resources["100"].fit
    )
    calls = {key: list(state[key]) for key in ("calls", "forecast")}
    before = {str(p): p.read_bytes() for p in root.rglob("*.json")}
    resumed = run(program)
    assert resumed["new_operations"] == 0 and resumed["completed_operations"] == 43
    assert calls == {key: state[key] for key in calls}
    assert before == {str(p): p.read_bytes() for p in root.rglob("*.json")}


@pytest.mark.parametrize(
    "attack",
    [
        "options",
        "source_session",
        "source_journal",
        "forecast_journal",
        "failed",
        "unfinished",
        "owner",
    ],
)
def test_changed_lost_failed_or_owned_program_never_restarts_operations(program, attack):
    root, options, policies, state = program
    if attack == "failed":
        state["fail"] = True
        with pytest.raises(SnapshotError, match="phase_failed"):
            run(program, 1)
    else:
        run(program, 6 if attack == "forecast_journal" else 1)
    if attack == "options":
        options = options.model_copy(update={"worker_python": "/changed-python"})
    elif attack == "source_session":
        shutil.rmtree(root / "25/source")
    elif attack == "source_journal":
        shutil.rmtree(root / "25/source/planning")
    elif attack == "forecast_journal":
        shutil.rmtree(root / "25/forecast")
    elif attack == "unfinished":
        path = root / "25/source/planning"
        journal.reserve(path, journal.inspect(path).protocol.operations[1].operation_id)
    before = {key: list(state[key]) for key in ("calls", "forecast")}
    if attack == "owner":
        with locked(root), pytest.raises(ValueError, match="still_active"):
            workflow.run_development_workflow(root, options, policies)
    else:
        with pytest.raises((SnapshotError, ValueError)):
            workflow.run_development_workflow(root, options, policies)
    assert before == {key: state[key] for key in before}


def test_future_budgets_and_effectively_distinct_recipes_required_before_any_source(program):
    _, options, _, state = program
    raw = options.model_dump(mode="json")
    raw["resources"]["100"]["fit"]["minimum_available_memory_bytes"] = 1024
    with pytest.raises(ValidationError, match="resource_limits"):
        DevelopmentWorkflowOptions.model_validate_json(json.dumps(raw))
    raw = options.model_dump(mode="json")
    raw["recipes"][1]["rf"] = raw["recipes"][0]["rf"]
    with pytest.raises(ValidationError, match="distinct_recipes"):
        DevelopmentWorkflowOptions.model_validate_json(json.dumps(raw))
    assert not state["calls"] and not state["forecast"]
