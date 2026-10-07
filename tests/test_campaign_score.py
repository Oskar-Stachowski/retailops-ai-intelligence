"""Audited ordering and durable receipts with mocked workers, never campaign results."""

from datetime import date
from pathlib import Path

import pytest
from test_campaign_export import mocked_pipeline, setup_campaign
from test_campaign_score_data import score_plan
from test_campaign_score_worker import fake_fits

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_export as exporter
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import campaign_score as runner
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastRawPrediction,
)
from retailops_ai.evaluation_campaign.campaign_score_metrics import RawMetrics
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError


def case(tmp_path, monkeypatch, *, finalize_document=None):
    seed = tmp_path / "metadata"
    seed.mkdir()
    _, previous, export_plan, generated = setup_campaign(seed)
    root = tmp_path.resolve() / "score-journal"
    plan = score_plan(
        source_recipe_sha256=export_plan.source_recipe_sha256,
        export_operation_id="development-42-read",
        fit_operation_ids={
            f: "development-fit" if f == "rf" else "development-fit-" + f for f in FAMILIES
        },
    )
    document = previous.model_dump(mode="json")
    document["journal_path"] = str(root)
    prototype = fake_fits("ai09-physical-forecast-sha256-" + "1" * 64)
    plans = {
        f: prototype[f].plan.model_copy(
            update={
                "family": f,
                "source_recipe_sha256": plan.source_recipe_sha256,
                "export_operation_id": plan.export_operation_id,
                "initialization_seed": 42,
            }
        )
        for f in FAMILIES
    }
    for operation in document["operations"]:
        if operation["action"] == "model_fit":
            operation["execution_recipe_sha256"] = plans[
                operation["forecast_family"]
            ].content_sha256()
    document["operations"].append(
        dict(
            operation_id="development-score",
            phase="development",
            action="model_score",
            use_case="forecast",
            role=plan.role,
            source_recipe_sha256=plan.source_recipe_sha256,
            execution_recipe_sha256=plan.content_sha256(),
            prerequisites=[plan.export_operation_id, *plan.fit_operation_ids.values()],
        )
    )
    document["maximum_new_attempts"] = (
        sum(o["maximum_attempts"] for o in document["operations"][:-1]) + 1
    )
    if finalize_document is not None:
        finalize_document(document)
    protocol = CampaignProtocol.model_validate_json(canonical_bytes(document))
    journal.initialize(root, protocol)
    start = journal.reserve(root, "development-42-generate")
    generated = generated.model_copy(
        update={
            "protocol_sha256": protocol.content_sha256(),
            "reservation_id": str(start.reservation_id),
            "runtime": protocol.runtime,
        }
    )
    journal.finish(
        root,
        str(start.reservation_id),
        result="completed",
        evidence_sha256=generated.content_sha256(),
        cost=CampaignCost(wall_seconds=0.01),
    )
    mocked_pipeline(monkeypatch, (root, protocol, export_plan, generated))
    dataset, exported = exporter.export_development_forecast(
        tmp_path,
        tmp_path,
        tmp_path,
        journal=root,
        operation_id=plan.export_operation_id,
        plan=export_plan,
        generated=generated,
    )
    fits = {}
    for family in FAMILIES:
        start = journal.reserve(root, plan.fit_operation_ids[family])
        fits[family] = prototype[family].model_copy(
            update={
                "operation_id": plan.fit_operation_ids[family],
                "reservation_id": str(start.reservation_id),
                "plan": plans[family],
                "protocol_sha256": protocol.content_sha256(),
                "runtime_code_sha256": protocol.runtime.code_sha256,
                "export_receipt_sha256": exported.content_sha256(),
                "dataset_id": exported.dataset_id,
            }
        )
        exporter._store_receipt(root, fits[family])
        journal.finish(
            root,
            str(start.reservation_id),
            result="completed",
            evidence_sha256=fits[family].content_sha256(),
            cost=CampaignCost(
                wall_seconds=0.01,
                peak_process_tree_rss_bytes=12345,
                artifact_bytes=fits[family].model_artifact_bytes,
            ),
        )
    output = tmp_path / "scores"
    output.mkdir(mode=0o700)

    def controlled_parent_verification(*args, **kwargs):
        assert journal.inspect(root).events[-1].kind == "reserved"
        assert journal.inspect(root).events[-1].operation_id == "development-score"

    monkeypatch.setattr(runner, "verify_campaign_forecast_bundle", controlled_parent_verification)
    return root, dataset, output, plan, exported, fits


def run(value, *, plan=None, fits=None, operation="development-score"):
    root, dataset, output, original, exported, parents = value
    return runner.score_campaign_forecast(
        dataset,
        {f: output / f for f in FAMILIES},
        Path("/unused-owned-python"),
        output,
        journal=root,
        operation_id=operation,
        plan=plan or original,
        exported=exported,
        fits=fits or parents,
    )


def fake_phases(monkeypatch, value, failure=None):
    root, _, _, plan, exported, fits = value
    row = CampaignForecastRawPrediction(
        product_id="product-a",
        selling_location_id="location-a",
        channel="store",
        forecast_origin=make_origin(date(2026, 3, 9)).forecast_origin,
        business_timezone="UTC",
        cutoff_policy="end_of_day_second_v1",
        target_date=date(2026, 3, 10),
        horizon_days=1,
        role="tune",
        example_sha256="1" * 64,
        eligible=True,
        exclusion_reasons=(),
        values=tuple(
            FunctionalForecast(mean=2.0, median=None if i == 3 else 1.0, interval=None)
            for i in range(6)
        ),
    )
    import hashlib

    digest = hashlib.sha256(membership_key(row) + b"\n").hexdigest()
    population = dict(
        rows=1,
        eligible_rows=1,
        keys_sha256=digest,
        eligible_keys_sha256=digest,
        role_population_sha256="2" * 64,
    )
    calls = []

    def monitor(command, **kwargs):
        phase, folder = command[-2], kwargs["root"]
        assert journal.inspect(root).events[-1].kind == "reserved"
        calls.append(phase)
        if failure == phase:
            return dict(
                status="failed", reason="controlled_worker_exit", sampled_tree_peak_rss_bytes=12345
            )
        if phase == "predict":
            bundle = folder / "bundle"
            bundle.mkdir(mode=0o700)
            write(bundle / "plan.json", plan.model_dump(mode="json"))
            write(
                bundle / "parents.json",
                {
                    "export_receipt_sha256": exported.content_sha256(),
                    "fit_receipt_sha256": {f: fits[f].content_sha256() for f in FAMILIES},
                    "model_artifact_sha256": {f: fits[f].model_artifact_sha256 for f in FAMILIES},
                },
            )
            metrics = RawMetrics()
            metrics.add(row, 1)
            write(bundle / "metrics.json", metrics.result())
            (bundle / "predictions.jsonl").write_bytes(
                canonical_bytes(row.model_dump(mode="json")) + b"\n"
            )
        write(
            folder / (phase + ".json"),
            population | dict(worker_peak_rss_bytes=12345, all_models_share_all_role_keys=True),
        )
        return dict(status="passed", reason=None, sampled_tree_peak_rss_bytes=12345)

    monkeypatch.setattr(runner, "monitor", monitor)
    return calls


def test_reserve_before_parent_reads_and_receipt_before_completion(tmp_path, monkeypatch):
    value = case(tmp_path, monkeypatch)
    calls = fake_phases(monkeypatch, value)
    bundle, receipt = run(value)
    assert calls == ["prepare", "predict"]
    runner.verify_campaign_forecast_scores(bundle, journal=value[0], receipt=receipt)
    assert receipt.rows == receipt.eligible_rows == 1 and not receipt.quality_qualified
    assert not receipt.calibration_fitted and not receipt.final_test_accessed
    completed = journal.inspect(value[0]).events[-1]
    assert completed.result == "completed" and completed.cost.peak_process_tree_rss_bytes == 12345
    assert (value[0] / "receipts" / (receipt.reservation_id + ".json")).is_file()
    (bundle / "predictions.jsonl").write_bytes(b"{}\n")
    with pytest.raises(SnapshotError, match="checksum"):
        runner.verify_campaign_forecast_scores(bundle, journal=value[0], receipt=receipt)


@pytest.mark.parametrize("phase", ["prepare", "predict"])
def test_failure_cost_is_preserved_without_completed_receipt(tmp_path, monkeypatch, phase):
    value = case(tmp_path, monkeypatch)
    fake_phases(monkeypatch, value, failure=phase)
    with pytest.raises(SnapshotError, match="phase_failed_" + phase):
        run(value)
    completed = journal.inspect(value[0]).events[-1]
    assert completed.result == "failed" and completed.cost.peak_process_tree_rss_bytes == 12345
    assert not (value[0] / "receipts" / (str(completed.reservation_id) + ".json")).exists()
    assert (value[0] / "receipts" / (value[4].reservation_id + ".json")).is_file()


@pytest.mark.parametrize("mutation", ["plan", "dataset", "family", "population"])
def test_wrong_frozen_binding_is_charged_before_any_worker(tmp_path, monkeypatch, mutation):
    value = case(tmp_path, monkeypatch)
    calls = fake_phases(monkeypatch, value)
    plan, fits = value[3], dict(value[5])
    if mutation == "plan":
        plan = plan.model_copy(update={"batch_windows": 1})
    elif mutation == "dataset":
        fits["rf"] = fits["rf"].model_copy(
            update={"dataset_id": "ai09-physical-forecast-sha256-" + "f" * 64}
        )
    elif mutation == "family":
        fits["rf"] = fits["rf"].model_copy(
            update={"plan": fits["rf"].plan.model_copy(update={"family": "hgb"})}
        )
    else:
        fits["rf"] = fits["rf"].model_copy(update={"train_keys_sha256": "f" * 64})
    with pytest.raises(SnapshotError, match="binding|populations"):
        run(value, plan=plan, fits=fits)
    assert not calls
    assert journal.inspect(value[0]).events[-1].result == "failed"


@pytest.mark.parametrize("operation", ["development-fit", "final-42-score-forecast", "unknown"])
def test_unrelated_operation_cannot_consume_another_grant(tmp_path, monkeypatch, operation):
    value = case(tmp_path, monkeypatch)
    before = (value[0] / "journal.json").read_bytes()
    with pytest.raises(SnapshotError, match="requires_development"):
        run(value, operation=operation)
    assert (value[0] / "journal.json").read_bytes() == before
