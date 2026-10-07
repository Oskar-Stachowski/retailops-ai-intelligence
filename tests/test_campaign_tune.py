"""Journal ordering/cost controls with mocked workers, not project selection results."""

import pytest
from pydantic import ValidationError
from test_campaign_score import case as score_case
from test_campaign_score import fake_phases
from test_campaign_score import run as run_score
from test_campaign_tune_data import tune_plan

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import campaign_tune as runner
from retailops_ai.evaluation_campaign.campaign_contract import CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_tune_data import BandMetrics, choose
from retailops_ai.source_snapshot.files import SnapshotError


def case(tmp_path, monkeypatch):
    saved = {}

    def finalize_document(document):
        score = document["operations"][-1]
        plan = tune_plan(
            (score["operation_id"],),
            source_recipe_sha256=score["source_recipe_sha256"],
            export_operation_id="development-42-read",
            campaign_selection_policy_sha256=document["selection_policy_sha256"],
            forecast_quality_policy_sha256=document["use_case_quality_policy_sha256"]["forecast"],
        )
        document["operations"].append(
            dict(
                operation_id="development-select",
                phase="development",
                action="model_score",
                use_case="forecast",
                role="tune",
                source_recipe_sha256=plan.source_recipe_sha256,
                execution_recipe_sha256=plan.content_sha256(),
                prerequisites=[plan.export_operation_id, *plan.score_operation_ids],
            )
        )
        document["maximum_new_attempts"] += 1
        saved["plan"] = plan

    value = score_case(tmp_path, monkeypatch, finalize_document=finalize_document)
    fake_phases(monkeypatch, value)
    bundle, receipt = run_score(value)
    output = tmp_path / "tune-output"
    output.mkdir(mode=0o700)
    return value[0], value[1], output, saved["plan"], value[4], bundle, receipt


def run(value, *, plan=None, scores=None, operation="development-select"):
    root, dataset, output, original, exported, bundle, score = value
    return runner.select_campaign_forecast(
        dataset,
        {score.operation_id: bundle},
        output / "unused-python",
        output,
        journal=root,
        operation_id=operation,
        plan=plan or original,
        exported=exported,
        scores=scores or {score.operation_id: score},
    )


def fake_worker(monkeypatch, value, *, failed=False, corrupt=False, peak=12345):
    root, _, _, plan, exported, original_bundle, score = value
    calls = []
    original_verify = runner.verify_campaign_forecast_scores

    def verify(*args, **kwargs):
        assert journal.inspect(root).events[-1].operation_id == "development-select"
        assert journal.inspect(root).events[-1].kind == "reserved"
        calls.append("parent")
        original_verify(*args, **kwargs)

    monkeypatch.setattr(runner, "verify_campaign_forecast_scores", verify)

    def monitor(command, **kwargs):
        assert journal.inspect(root).events[-1].kind == "reserved"
        calls.append("worker")
        if failed:
            return dict(
                status="failed",
                reason="controlled_worker_exit",
                wall_seconds=0.1,
                sampled_tree_peak_rss_bytes=12345,
            )
        folder = kwargs["root"]
        metrics = read(original_bundle / "metrics.json")
        for segment in metrics["segments"]:
            for model in segment["models"].values():
                model["interval"] = BandMetrics().result(segment["eligible_rows"])
        trial = dict(
            score_operation_id=score.operation_id,
            metrics=metrics,
            rows=score.rows,
            eligible_rows=score.eligible_rows,
            keys_sha256=score.keys_sha256,
            eligible_keys_sha256=score.eligible_keys_sha256,
            role_population_sha256=score.role_population_sha256,
            baseline_predictions_sha256="4" * 64,
        )
        selected = choose([trial], {score.operation_id: score}, plan)
        bundle = folder / "bundle"
        bundle.mkdir(mode=0o700)
        write(bundle / "plan.json", plan.model_dump(mode="json"))
        write(
            bundle / "parents.json",
            {
                "export_receipt_sha256": exported.content_sha256(),
                "scores": {score.operation_id: score.model_dump(mode="json")},
            },
        )
        write(bundle / "metrics.json", {"trials": [trial]})
        write(bundle / "selection.json", selected.model_dump(mode="json"))
        write(
            folder / "select.json",
            {
                **{
                    k: trial[k]
                    for k in (
                        "rows",
                        "eligible_rows",
                        "keys_sha256",
                        "eligible_keys_sha256",
                        "role_population_sha256",
                        "baseline_predictions_sha256",
                    )
                },
                "selection": selected.model_dump(mode="json"),
                "worker_peak_rss_bytes": peak,
                "trial_count": 1,
                "full_tune_label_passes": 1,
                "calibration_label_passes": 1 if corrupt else 0,
                "independent_or_final_label_passes": 0,
            },
        )
        return dict(
            status="passed", reason=None, wall_seconds=0.1, sampled_tree_peak_rss_bytes=12345
        )

    monkeypatch.setattr(runner, "monitor", monitor)
    return calls


def test_reserves_before_parent_reads_and_stores_receipt_before_completion(tmp_path, monkeypatch):
    value = case(tmp_path, monkeypatch)
    calls = fake_worker(monkeypatch, value)
    bundle, receipt = run(value)
    assert calls == ["parent", "worker"]
    assert receipt.selection.status == "not_ready" and not receipt.stage_ready
    runner.verify_campaign_forecast_selection(bundle, journal=value[0], receipt=receipt)
    completed = journal.inspect(value[0]).events[-1]
    assert completed.result == "completed" and completed.evidence_sha256 == receipt.content_sha256()
    assert completed.cost.peak_process_tree_rss_bytes == 12345 and completed.cost.artifact_bytes > 0
    with pytest.raises(ValidationError, match="budget_exhausted"):
        run(value)
    (bundle / "selection.json").write_bytes(b"{}\n")
    with pytest.raises(SnapshotError, match="checksum"):
        runner.verify_campaign_forecast_selection(bundle, journal=value[0], receipt=receipt)


@pytest.mark.parametrize("corrupt", [False, True])
def test_failed_worker_or_forbidden_role_exposure_preserves_cost_without_completion(
    tmp_path, monkeypatch, corrupt
):
    value = case(tmp_path, monkeypatch)
    fake_worker(monkeypatch, value, failed=not corrupt, corrupt=corrupt)
    with pytest.raises(SnapshotError, match="worker_failed|scope_or_peak"):
        run(value)
    completed = journal.inspect(value[0]).events[-1]
    assert completed.result == "failed" and completed.cost.peak_process_tree_rss_bytes == 12345
    assert not (value[0] / "receipts" / (str(completed.reservation_id) + ".json")).exists()
    assert (value[0] / "receipts" / (value[6].reservation_id + ".json")).is_file()


@pytest.mark.parametrize("mutation", ["plan", "source", "population", "dataset", "role"])
def test_frozen_binding_failure_is_charged_before_worker_or_label_reads(
    tmp_path, monkeypatch, mutation
):
    value = case(tmp_path, monkeypatch)
    calls = fake_worker(monkeypatch, value)
    plan, score = value[3], value[6]
    if mutation == "plan":
        plan = plan.model_copy(update={"max_rows": 1})
    elif mutation == "source":
        plan = plan.model_copy(update={"campaign_selection_policy_sha256": "f" * 64})
    elif mutation == "population":
        score = score.model_copy(update={"eligible_keys_sha256": "f" * 64})
    elif mutation == "dataset":
        score = score.model_copy(update={"dataset_id": "ai09-physical-forecast-sha256-" + "f" * 64})
    else:
        score = score.model_copy(
            update={"plan": score.plan.model_copy(update={"role": "calibration"})}
        )
    with pytest.raises(SnapshotError):
        run(value, plan=plan, scores={score.operation_id: score})
    if mutation == "population":
        assert calls == ["parent"]
    else:
        assert not calls
    assert journal.inspect(value[0]).events[-1].result == "failed"


@pytest.mark.parametrize("operation", ["development-fit", "final-42-score-forecast", "unknown"])
def test_unrelated_operations_cannot_consume_another_grant(tmp_path, monkeypatch, operation):
    value = case(tmp_path, monkeypatch)
    before = (value[0] / "journal.json").read_bytes()
    with pytest.raises(SnapshotError, match="requires_development"):
        run(value, operation=operation)
    assert (value[0] / "journal.json").read_bytes() == before


def test_selection_cannot_silently_omit_a_preregistered_triplet(tmp_path, monkeypatch):
    value = case(tmp_path, monkeypatch)
    ledger = journal.inspect(value[0])
    document = ledger.protocol.model_dump(mode="json")
    extras = [
        {**operation, "operation_id": operation["operation_id"] + "-another-trial"}
        for operation in document["operations"]
        if operation["action"] == "model_fit"
    ]
    document["operations"].extend(extras)
    extra_attempts = sum(operation["maximum_attempts"] for operation in extras)
    document["maximum_new_attempts"] += extra_attempts
    document["maximum_new_fit_attempts"] += extra_attempts
    protocol = CampaignProtocol.model_validate_json(canonical_bytes(document))
    ledger = ledger.model_copy(
        update={"protocol": protocol, "protocol_sha256": protocol.content_sha256()}
    )
    exported = value[4].model_copy(update={"protocol_sha256": protocol.content_sha256()})
    score = value[6].model_copy(
        update={
            "protocol_sha256": protocol.content_sha256(),
            "export_receipt_sha256": exported.content_sha256(),
        }
    )
    with pytest.raises(SnapshotError, match="every_frozen_trial_once"):
        runner._binding(
            ledger,
            runner._operation(ledger, "development-select"),
            value[3],
            exported,
            {score.operation_id: score},
        )


@pytest.mark.parametrize("over_budget", [False, True])
def test_worker_system_peak_is_retained_even_when_sampling_misses_it(
    tmp_path, monkeypatch, over_budget
):
    value = case(tmp_path, monkeypatch)
    peak = value[3].resources.tree_rss_bytes + 1 if over_budget else 23456
    fake_worker(monkeypatch, value, peak=peak)
    if over_budget:
        with pytest.raises(SnapshotError, match="scope_or_peak"):
            run(value)
    else:
        run(value)
    completion = journal.inspect(value[0]).events[-1]
    assert completion.result == ("failed" if over_budget else "completed")
    assert completion.cost.peak_process_tree_rss_bytes == peak
