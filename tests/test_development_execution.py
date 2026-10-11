"""Driver controls use real durable journals and declared native Source doubles."""

import hashlib
from pathlib import Path

import pytest
from test_development_screening import native_double, setup

from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import development_execution as runner
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_export import export_development_forecast
from retailops_ai.evaluation_campaign.development_advancement import freeze_multiscale_selection
from retailops_ai.evaluation_campaign.development_advancement_contract import ForecastScaleBudget
from retailops_ai.evaluation_campaign.development_screening_contract import screening_operations
from retailops_ai.evaluation_campaign.development_screening_selection_contract import (
    freeze_screening_selection,
)
from retailops_ai.evaluation_campaign.preparation_execution import locked
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.pipeline import environment_lock


@pytest.fixture
def execution(tmp_path, monkeypatch):
    _, prefix, output = setup(tmp_path, monkeypatch)
    root = tmp_path / "driver-journal"
    common_lock = hashlib.sha256(environment_lock()).hexdigest()
    fits = tuple(
        f.model_copy(update={"worker_environment_lock_sha256": common_lock}) for f in prefix.fits
    )
    scores = tuple(
        s.model_copy(update={"worker_environment_lock_sha256": common_lock}) for s in prefix.scores
    )
    prefix = prefix.model_copy(
        update={
            "journal_path": str(root),
            "fits": fits,
            "scores": scores,
            "operations": screening_operations(prefix.sources, prefix.exports, fits, scores),
        }
    )

    def budget(products):
        return ForecastScaleBudget(
            bootstrap_journal_path=str(tmp_path / str(products) / "bootstrap"),
            preparation_journal_path=str(tmp_path / str(products) / "preparation"),
            execution_journal_path=str(tmp_path / str(products) / "execution"),
            preparation=prefix.parents.preparation.bootstrap.generation.resources,
            fit=prefix.fits[0].resources,
            score=prefix.scores[0].resources,
            maximum_total_preparation_wall_seconds=3600,
        )

    protocol = freeze_multiscale_selection(
        freeze_screening_selection(prefix), fifty=budget(50), hundred=budget(100)
    )
    journal.initialize(root, protocol)
    paths = runner.DevelopmentExecutionPaths(
        protocol_sha256=protocol.content_sha256(),
        parents=tuple(
            runner.DevelopmentParentPaths(
                snapshot=str(tmp_path / str(i) / "snapshot"),
                curated=str(tmp_path / str(i) / "curated"),
            )
            for i in range(3)
        ),
        worker_python="/declared-worker-python",
        output_root=str(output),
    )
    calls = []

    def exported(snapshot, curated, destination, **kwargs):
        position = next(i for i, p in enumerate(prefix.exports) if p == kwargs["plan"])
        assert snapshot == Path(paths.parents[position].snapshot)
        assert curated == Path(paths.parents[position].curated)
        calls.append(position)
        native_double(monkeypatch, (root, prefix, output), position)
        return export_development_forecast(snapshot, curated, destination, **kwargs)

    monkeypatch.setattr(runner, "export_development_forecast", exported)
    return root, paths, calls


def test_clean_stop_resumes_next_original_operation_without_resetting_cost(execution):
    root, paths, calls = execution
    first = runner.run_development_forecast(root, paths, maximum_new_operations=1)
    before = journal.inspect(root)
    second = runner.run_development_forecast(root, paths, maximum_new_operations=1)
    after = journal.inspect(root)
    assert calls == [0, 1]
    assert first["completed_operations"] == first["new_operations"] == 1
    assert second["completed_operations"] == 2 and second["new_operations"] == 1
    assert second["status"] == "incomplete" and not second["stage_ready"]
    assert after.events[:2] == before.events
    assert before.events[-1].cost.peak_process_tree_rss_bytes > 0


@pytest.mark.parametrize("field", ["worker_python", "output_root", "protocol_sha256"])
def test_paths_and_protocol_cannot_be_changed_after_first_operation(execution, field):
    root, paths, calls = execution
    runner.run_development_forecast(root, paths, maximum_new_operations=1)
    before = journal.inspect(root)
    value = "f" * 64 if field == "protocol_sha256" else getattr(paths, field) + "-changed"
    changed = paths.model_copy(update={field: value})
    with pytest.raises(SnapshotError, match="wrong_protocol|frozen_paths_changed"):
        runner.run_development_forecast(root, changed)
    assert journal.inspect(root) == before and calls == [0]


def test_failed_controller_is_retained_and_never_retried(execution, monkeypatch):
    root, paths, calls = execution

    def fail(*args, **kwargs):
        calls.append("failed")
        with journal.audited_operation(root, kwargs["operation_id"]):
            raise SnapshotError("declared_native_failure")

    monkeypatch.setattr(runner, "export_development_forecast", fail)
    with pytest.raises(SnapshotError, match="declared_native_failure"):
        runner.run_development_forecast(root, paths)
    before = journal.inspect(root)
    assert before.events[-1].result == "failed"
    with pytest.raises(SnapshotError, match="failed_or_unordered_attempt_no_retry"):
        runner.run_development_forecast(root, paths)
    assert journal.inspect(root) == before and calls == ["failed"]


def test_unfinished_attempt_and_active_owner_block_second_driver(execution):
    root, paths, calls = execution
    with locked(root), pytest.raises(ValueError, match="owned_operation_still_active"):
        runner.run_development_forecast(root, paths)
    protocol = journal.inspect(root).protocol
    journal.reserve(root, protocol.operations[0].operation_id)
    before = journal.inspect(root)
    with pytest.raises(SnapshotError, match="unfinished_attempt_no_automatic_retry"):
        runner.run_development_forecast(root, paths)
    assert journal.inspect(root) == before and calls == []


@pytest.mark.parametrize("missing", ["receipt", "manifest"])
def test_original_missing_evidence_blocks_next_operation(execution, missing):
    root, paths, calls = execution
    runner.run_development_forecast(root, paths, maximum_new_operations=1)
    before = journal.inspect(root)
    reservation = before.events[-1].reservation_id
    if missing == "receipt":
        (root / "receipts" / (reservation + ".json")).unlink()
    else:
        next(Path(paths.output_root).glob("ai09-physical-*/manifest.json")).unlink()
    with pytest.raises((OSError, SnapshotError)):
        runner.run_development_forecast(root, paths)
    assert journal.inspect(root) == before and calls == [0]


def test_controller_return_without_durable_completion_cannot_advance(execution, monkeypatch):
    root, paths, calls = execution
    monkeypatch.setattr(runner, "export_development_forecast", lambda *a, **k: (None, None))
    with pytest.raises(SnapshotError, match="controller_completion_required"):
        runner.run_development_forecast(root, paths)
    assert not journal.inspect(root).events and calls == []


def test_output_cannot_write_into_original_parent_or_journal(execution):
    root, paths, calls = execution
    for destination in (root / "outputs", Path(paths.parents[0].snapshot) / "outputs"):
        changed = paths.model_copy(update={"output_root": str(destination)})
        with pytest.raises(SnapshotError, match="overlaps_original_evidence"):
            runner.run_development_forecast(root, changed)
    assert not journal.inspect(root).events and calls == []


def test_full_driver_routes_all_trials_and_selection_and_resume_starts_no_worker(
    execution, monkeypatch
):
    """Model artifacts are explicit doubles; score/selection controllers and ancestry stay real."""
    from test_campaign_score_worker import fake_fits
    from test_development_advancement import full_score_phases, paired_selection_worker

    from retailops_ai.evaluation_campaign import campaign_export as exporter
    from retailops_ai.evaluation_campaign import campaign_score as scorer
    from retailops_ai.evaluation_campaign import campaign_tune as tuner
    from retailops_ai.evaluation_campaign.campaign_fit import validate_completed_fit

    root, paths, calls = execution
    model_calls, score_calls, selection_calls = [], [], []
    journal_api = journal

    def fit(dataset, worker_python, output, *, journal, operation_id, plan, exported):
        assert journal == root and worker_python == Path(paths.worker_python)
        assert dataset == output / exported.dataset_id
        start = journal_api.reserve(root, operation_id)
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
        exporter._store_receipt(root, receipt)
        journal_api.finish(
            root,
            receipt.reservation_id,
            result="completed",
            evidence_sha256=receipt.content_sha256(),
            cost=CampaignCost(
                wall_seconds=0.01,
                peak_process_tree_rss_bytes=12345,
                artifact_bytes=receipt.model_artifact_bytes,
            ),
        )
        model_calls.append(operation_id)
        return output / receipt.reservation_id / "bundle", receipt

    def verify_declared_fit(bundle, *, journal, receipt):
        assert bundle == Path(paths.output_root) / receipt.reservation_id / "bundle"
        validate_completed_fit(journal, receipt)

    def score(dataset, bundles, worker_python, output, **kwargs):
        full_score_phases(
            monkeypatch, (root, dataset, output, kwargs["plan"], kwargs["exported"], kwargs["fits"])
        )
        score_calls.append(kwargs["operation_id"])
        return scorer.score_campaign_forecast(dataset, bundles, worker_python, output, **kwargs)

    def select(dataset, bundles, worker_python, output, **kwargs):
        paired_selection_worker(monkeypatch, root, kwargs["scores"], kwargs["exported"])
        selection_calls.append(kwargs["operation_id"])
        return tuner.select_campaign_forecast(dataset, bundles, worker_python, output, **kwargs)

    monkeypatch.setattr(runner, "fit_campaign_forecast", fit)
    monkeypatch.setattr(runner, "score_campaign_forecast", score)
    monkeypatch.setattr(runner, "select_campaign_forecast", select)
    monkeypatch.setattr(runner, "verify_campaign_forecast_bundle", verify_declared_fit)
    monkeypatch.setattr(scorer, "verify_campaign_forecast_bundle", verify_declared_fit)
    result = runner.run_development_forecast(root, paths)
    before = journal.inspect(root)
    assert result["status"] == "completed" and result["new_operations"] == 12
    assert not result["quality_qualified"] and not result["final_test_access_authorized"]
    assert (
        calls == [0, 1, 2]
        and len(model_calls) == 6
        and len(score_calls) == 2
        and len(selection_calls) == 1
    )
    original_result = (root / "forecast-execution-result.json").read_bytes()
    resumed = runner.run_development_forecast(root, paths)
    assert resumed["status"] == "completed" and resumed["new_operations"] == 0
    assert (
        journal.inspect(root) == before
        and (root / "forecast-execution-result.json").read_bytes() == original_result
    )
    assert (
        calls == [0, 1, 2]
        and len(model_calls) == 6
        and len(score_calls) == 2
        and len(selection_calls) == 1
    )
