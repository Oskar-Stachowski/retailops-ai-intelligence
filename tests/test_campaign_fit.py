"""Audited fit ordering and charged failures; mocked workers are never campaign evidence."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from test_campaign_export import mocked_pipeline, setup_campaign
from test_campaign_fit_data import fit_plan

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_export as exporter
from retailops_ai.evaluation_campaign import campaign_fit as runner
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_fit_contract import (
    CampaignCategoricalEncoding,
    CampaignForecastEncoding,
    CampaignNumericEncoding,
)
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.forecasting.features_contract import FEATURE_TYPES
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.pipeline import environment_lock


def encoding():
    numeric = tuple(
        CampaignNumericEncoding(name=n, fill=0.0, known_count=0, center=0.0, spread=1.0)
        for n, kind in FEATURE_TYPES.items()
        if kind != "str"
    )
    categorical = tuple(
        CampaignCategoricalEncoding(name=n, categories=())
        for n, kind in FEATURE_TYPES.items()
        if kind == "str"
    )
    columns = tuple(c for item in numeric for c in (item.name, item.name + "__missing")) + tuple(
        c for item in categorical for c in (item.name + "__missing", item.name + "__unknown")
    )
    return CampaignForecastEncoding(
        train_keys_sha256="b" * 64,
        train_labels_sha256="c" * 64,
        train_rows=2,
        numeric=numeric,
        categorical=categorical,
        history_fill=0.0,
        history_center=0.0,
        history_spread=1.0,
        target_scale=1.0,
        output_columns=columns,
    )


def case(tmp_path, monkeypatch):
    seed = tmp_path / "declared-seed"
    seed.mkdir()
    _, previous, export_plan, generated = setup_campaign(seed)
    root = tmp_path.resolve() / "fit-journal"
    plan = fit_plan().model_copy(
        update={
            "source_recipe_sha256": export_plan.source_recipe_sha256,
            "export_operation_id": "development-42-read",
        }
    )
    document = previous.model_dump(mode="json")
    document["journal_path"] = str(root)
    for op in document["operations"]:
        if op["action"] == "model_fit":
            op["maximum_attempts"] = 1
            op["initialization_seed"] = plan.initialization_seed
        if op["operation_id"] == "development-fit":
            op["execution_recipe_sha256"] = plan.content_sha256()
    document["maximum_new_fit_attempts"] = 3
    document["maximum_new_attempts"] = sum(op["maximum_attempts"] for op in document["operations"])
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
    export_case = root, protocol, export_plan, generated
    mocked_pipeline(monkeypatch, export_case)
    dataset, exported = exporter.export_development_forecast(
        tmp_path,
        tmp_path,
        tmp_path,
        journal=root,
        operation_id="development-42-read",
        plan=export_plan,
        generated=generated,
    )
    output = tmp_path / "fits"
    output.mkdir(mode=0o700)
    return root, dataset, output, plan, exported


def run(value, *, plan=None, operation="development-fit"):
    root, dataset, output, original, exported = value
    return runner.fit_campaign_forecast(
        dataset,
        Path("/unused-owned-python"),
        output,
        journal=root,
        operation_id=operation,
        plan=plan or original,
        exported=exported,
    )


def fake_phases(monkeypatch, value, *, failure=None, wrong_reload=False):
    root, _, _, plan, exported = value
    observed = []

    def monitor(command, **kwargs):
        phase, folder = command[-2], kwargs["root"]
        event = journal.inspect(root).events[-1]
        assert event.kind == "reserved" and event.operation_id == "development-fit"
        observed.append(phase)
        if phase == failure:
            return {
                "status": "failed",
                "reason": "controlled_worker_failure",
                "sampled_tree_peak_rss_bytes": 12345,
                "wall_seconds": 0.01,
            }
        result = {"worker_peak_rss_bytes": 12345}
        if phase == "prepare":
            bundle = folder / "bundle"
            bundle.mkdir(mode=0o700)
            state = encoding()
            write(bundle / "encoding.json", state.model_dump(mode="json"))
            write(bundle / "plan.json", plan.model_dump(mode="json"))
            write(
                bundle / "binding.json",
                {
                    "export_receipt_sha256": exported.content_sha256(),
                    "dataset_id": exported.dataset_id,
                    "source_recipe_sha256": plan.source_recipe_sha256,
                    "runtime_code_sha256": exported.runtime_code_sha256,
                },
            )
            (bundle / "dependencies.lock").write_bytes(environment_lock())
            result.update(
                encoding_sha256=state.content_sha256(),
                matrices={
                    "train": {"rows": 2, "keys_sha256": state.train_keys_sha256},
                    "early_stopping": {"rows": 2, "keys_sha256": "d" * 64},
                },
            )
        elif phase == "fit":
            write(
                folder / "bundle" / "mean.json",
                {
                    "family": "random_forest",
                    "feature_count": len(encoding().output_columns) + 1,
                    "input_dtype": "float32",
                    "aggregation": "mean",
                    "intercept": 0.0,
                    "trees": [
                        {
                            "nodes": [
                                {
                                    "value": 7.0,
                                    "feature": None,
                                    "threshold": None,
                                    "left": None,
                                    "right": None,
                                }
                            ]
                        }
                    ],
                },
            )
        else:
            result.update(
                reload_verified_eligible_rows=1 if wrong_reload else 2,
                reload_all_early_stopping_keys_verified=True,
            )
        write(folder / (phase + ".json"), result)
        return {"status": "passed", "sampled_tree_peak_rss_bytes": 12345, "wall_seconds": 0.01}

    monkeypatch.setattr(runner, "monitor", monitor)
    return observed


@pytest.mark.parametrize("operation", ["development-42-read", "final-42-score-forecast", "unknown"])
def test_wrong_operation_never_charges_an_unrelated_budget(tmp_path, monkeypatch, operation):
    value = case(tmp_path, monkeypatch)
    before = journal.inspect(value[0]).head_sha256
    with pytest.raises(SnapshotError, match="development_forecast_fit"):
        run(value, operation=operation)
    assert journal.inspect(value[0]).head_sha256 == before


@pytest.mark.parametrize(
    "field,changed",
    [
        ("family", "hgb"),
        ("initialization_seed", 2026),
        ("max_train_rows", 1999999),
        ("export_operation_id", "different-export"),
    ],
)
def test_changed_frozen_plan_is_charged_before_any_worker_and_cannot_retry(
    tmp_path, monkeypatch, field, changed
):
    value = case(tmp_path, monkeypatch)
    observed = fake_phases(monkeypatch, value)
    with pytest.raises(SnapshotError, match="frozen_plan_or_export_binding"):
        run(value, plan=value[3].model_copy(update={field: changed}))
    assert not observed
    assert journal.inspect(value[0]).events[-1].result == "failed"
    with pytest.raises(ValidationError, match="budget_exhausted"):
        run(value)


@pytest.mark.parametrize("failure", ["prepare", "fit", "reload", "receipt"])
def test_worker_or_publication_failure_cannot_create_a_completed_fit(
    tmp_path, monkeypatch, failure
):
    value = case(tmp_path, monkeypatch)
    fake_phases(monkeypatch, value, failure=failure)
    if failure == "receipt":
        monkeypatch.setattr(
            runner,
            "_store_receipt",
            lambda *args: (_ for _ in ()).throw(OSError("controlled_publish_failure")),
        )
    with pytest.raises((SnapshotError, OSError)):
        run(value)
    event = journal.inspect(value[0]).events[-1]
    assert event.result == "failed" and event.cost.wall_seconds > 0
    assert not (value[0] / "receipts" / (event.reservation_id + ".json")).exists()
    assert (value[0] / "receipts" / (value[4].reservation_id + ".json")).exists()


def test_reload_must_cover_every_eligible_key_before_completion(tmp_path, monkeypatch):
    value = case(tmp_path, monkeypatch)
    fake_phases(monkeypatch, value, wrong_reload=True)
    with pytest.raises(SnapshotError, match="reload_population_mismatch"):
        run(value)
    assert journal.inspect(value[0]).events[-1].result == "failed"


def test_complete_fit_receipt_is_durable_measured_and_rejects_tampering(tmp_path, monkeypatch):
    value = case(tmp_path, monkeypatch)
    observed = fake_phases(monkeypatch, value)
    bundle, receipt = run(value)
    assert observed == ["prepare", "fit", "reload"] and bundle.is_dir()
    assert receipt.train_eligible_rows == receipt.early_stopping_eligible_rows == 2
    assert (
        not receipt.final_test_accessed
        and not receipt.quality_qualified
        and not receipt.stage_ready
    )
    event = journal.inspect(value[0]).events[-1]
    assert event.result == "completed" and event.cost.peak_process_tree_rss_bytes == 12345
    runner.validate_completed_fit(value[0], receipt)
    runner.verify_campaign_forecast_bundle(bundle, journal=value[0], receipt=receipt)
    tree = bundle / "mean.json"
    tree.write_bytes(tree.read_bytes() + b" ")
    with pytest.raises(SnapshotError, match="artifact_checksum_mismatch"):
        runner.verify_campaign_forecast_bundle(bundle, journal=value[0], receipt=receipt)
    stored = value[0] / "receipts" / (receipt.reservation_id + ".json")
    assert stored.stat().st_mode & 0o777 == 0o600
    stored.write_bytes(stored.read_bytes() + b" ")
    with pytest.raises(SnapshotError, match="stored_receipt_mismatch"):
        runner.validate_completed_fit(value[0], receipt)
