"""Controlled journal ordering/failure accounting; mocked parents are not science."""

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError
from test_campaign_anomaly_fit_data import fit_plan
from test_campaign_export import mocked_pipeline, setup_campaign

from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_anomaly_fit as runner
from retailops_ai.evaluation_campaign import campaign_export as exporter
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.source_snapshot.files import SnapshotError


def case(tmp_path, monkeypatch):
    seed = tmp_path / "declared-control"
    seed.mkdir()
    _, previous, export_plan, generated = setup_campaign(seed)
    root = tmp_path.resolve() / "anomaly-journal"
    plan = fit_plan(
        source_recipe_sha256=export_plan.source_recipe_sha256,
        validation=Window(start=date(2026, 7, 23), end=date(2026, 7, 25)),
        test=Window(start=date(2026, 7, 29), end=date(2026, 7, 31)),
        selection_cutoff=datetime(2026, 7, 29, tzinfo=UTC),
    )
    document = previous.model_dump(mode="json")
    document["journal_path"] = str(root)
    document["operations"].append(
        {
            "operation_id": "development-anomaly-fit",
            "phase": "development",
            "action": "model_fit",
            "use_case": "anomaly",
            "role": "train",
            "source_recipe_sha256": plan.source_recipe_sha256,
            "execution_recipe_sha256": plan.content_sha256(),
            "initialization_seed": plan.policy.model_seed,
            "maximum_attempts": 1,
            "prerequisites": [plan.export_operation_id],
        }
    )
    document["maximum_new_attempts"] += 1
    document["maximum_new_fit_attempts"] += 1
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
    _, exported = exporter.export_development_forecast(
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
    return root, output, plan, exported


def run(value, *, plan=None, exported=None, operation="development-anomaly-fit"):
    root, output, original, parent = value
    return runner.fit_campaign_anomaly(
        output / "never-opened-snapshot",
        output / "never-opened-curated",
        output,
        journal=root,
        operation_id=operation,
        plan=plan or original,
        exported=exported or parent,
    )


def assert_failed(root):
    last = journal.inspect(root).events[-2:]
    assert last[0].kind == "reserved" and last[0].operation_id == "development-anomaly-fit"
    assert last[1].kind == "finished" and last[1].result == "failed"
    assert last[1].cost is not None and last[1].cost.wall_seconds > 0
    assert not (root / "receipts" / (str(last[1].reservation_id) + ".json")).exists()


@pytest.mark.parametrize(
    "operation", ["development-fit", "development-42-read", "final-42-score-anomaly", "unknown"]
)
def test_wrong_operation_does_not_consume_an_unrelated_budget(tmp_path, monkeypatch, operation):
    value = case(tmp_path, monkeypatch)
    before = journal.inspect(value[0]).head_sha256
    with pytest.raises(SnapshotError, match="requires_development_anomaly_train"):
        run(value, operation=operation)
    assert journal.inspect(value[0]).head_sha256 == before


@pytest.mark.parametrize("change", ["plan", "seed", "receipt", "stored_receipt"])
def test_bad_plan_or_incomplete_parent_charged_before_source_io(tmp_path, monkeypatch, change):
    value = case(tmp_path, monkeypatch)
    root, _, plan, exported = value

    def forbidden(*args, **kwargs):
        pytest.fail("worker or Source read started before binding checks")

    monkeypatch.setattr(runner, "monitor", forbidden)
    if change == "plan":
        plan = plan.model_copy(update={"source_recipe_sha256": "0" * 64})
    elif change == "seed":
        plan = plan.model_copy(update={"policy": plan.policy.model_copy(update={"model_seed": 17})})
    elif change == "receipt":
        exported = exported.model_copy(update={"protocol_sha256": "0" * 64})
    else:
        (root / "receipts" / (exported.reservation_id + ".json")).unlink()
    with pytest.raises(SnapshotError):
        run(value, plan=plan, exported=exported)
    assert_failed(root)
    before_retry = journal.inspect(root).head_sha256
    with pytest.raises(ValidationError, match="campaign_operation_budget_exhausted"):
        run(value)
    assert journal.inspect(root).head_sha256 == before_retry


@pytest.mark.parametrize(
    "failure", ["fit", "reload", "partial_reload", "parent_incomplete", "missing_family", "empty"]
)
def test_failed_or_incomplete_worker_never_completes_fit(tmp_path, monkeypatch, failure):
    value = case(tmp_path, monkeypatch)
    root = value[0]
    phases = []

    def controlled(command, **kwargs):
        phase, folder = command[-2], kwargs["root"]
        last = journal.inspect(root).events[-1]
        assert last.kind == "reserved" and last.operation_id == "development-anomaly-fit"
        phases.append(phase)
        measured = {
            "status": "failed" if failure == phase else "passed",
            "sampled_tree_peak_rss_bytes": 12345,
            "wall_seconds": 0.1,
        }
        if measured["status"] == "passed":
            result = {
                "worker_peak_rss_bytes": 10000,
                "conservative_worker_tree_peak_rss_bytes": 15000,
                "model_id": "anomaly-detector-sha256-" + "5" * 64,
            }
            if phase == "fit":
                result.update(
                    all_parent_contexts_completed=failure != "parent_incomplete",
                    validation_files={
                        "validation-" + family + ".jsonl": {"rows": 0 if failure == "empty" else 2}
                        for family in ("isolation_forest", "seasonal_residual")
                        if failure != "missing_family" or family == "isolation_forest"
                    },
                )
            else:
                result.update(
                    all_validation_rows_replayed=True,
                    rows={
                        "validation-" + family + ".jsonl": (
                            1 if failure == "partial_reload" else 0 if failure == "empty" else 2
                        )
                        for family in ("isolation_forest", "seasonal_residual")
                        if failure != "missing_family" or family == "isolation_forest"
                    },
                )
            write(folder / (phase + ".json"), result)
        return measured

    monkeypatch.setattr(runner, "monitor", controlled)
    with pytest.raises(SnapshotError, match="phase_failed|incomplete_parent_or_reload"):
        run(value)
    assert phases == (["fit"] if failure == "fit" else ["fit", "reload"])
    assert_failed(root)
    assert journal.inspect(root).events[-1].cost.peak_process_tree_rss_bytes >= 12345


def test_success_is_durable_and_verified_before_completion_and_cannot_be_repeated(
    tmp_path, monkeypatch
):
    value = case(tmp_path, monkeypatch)
    root = value[0]
    verified = []
    rows = {"validation-" + family + ".jsonl": 2 for family in runner.FAMILIES}

    def controlled(command, **kwargs):
        phase, folder = command[-2], kwargs["root"]
        result = {
            "conservative_worker_tree_peak_rss_bytes": 15000,
            "model_id": "anomaly-detector-sha256-" + "5" * 64,
        }
        if phase == "fit":
            bundle = folder / "bundle"
            bundle.mkdir(mode=0o700)
            for name in (
                "model.json",
                "feature-manifest.json",
                "parent-completion.json",
                "plan.json",
            ):
                write(bundle / name, {"control": name})
            result.update(
                all_parent_contexts_completed=True,
                feature_manifest_sha256="6" * 64,
                validation_files={name: {"rows": count} for name, count in rows.items()},
            )
        else:
            result.update(all_validation_rows_replayed=True, rows=rows)
        write(folder / (phase + ".json"), result)
        return {"status": "passed", "sampled_tree_peak_rss_bytes": 12345, "wall_seconds": 0.1}

    def controlled_binding(bundle, receipt):
        # Actual numerical/parent binding is covered by the physical tests.
        # Here the real journal and durable file protocol are exercised.
        last = journal.inspect(root).events[-1]
        assert last.kind == "reserved" and last.operation_id == "development-anomaly-fit"
        assert not (root / "receipts" / (receipt.reservation_id + ".json")).exists()
        assert runner._bundle_inventory(bundle, receipt.plan.max_artifact_bytes) == (
            receipt.artifact_files,
            receipt.model_artifact_bytes,
        )
        verified.append(receipt.content_sha256())

    monkeypatch.setattr(runner, "monitor", controlled)
    monkeypatch.setattr(runner, "_verify_bundle_content", controlled_binding)
    bundle, receipt = run(value)
    completion = journal.inspect(root).events[-1]
    assert bundle.is_dir()
    assert verified == [receipt.content_sha256()]
    assert completion.result == "completed" and completion.evidence_sha256 == verified[0]
    assert completion.cost.artifact_bytes == receipt.model_artifact_bytes
    assert completion.cost.peak_process_tree_rss_bytes == 15000
    runner.validate_completed_anomaly_fit(root, receipt)
    stored = root / "receipts" / (receipt.reservation_id + ".json")
    assert stored.stat().st_mode & 0o777 == 0o600
    before_retry = journal.inspect(root).head_sha256
    with pytest.raises(ValidationError, match="campaign_operation_budget_exhausted"):
        run(value)
    assert journal.inspect(root).head_sha256 == before_retry
    stored.write_bytes(stored.read_bytes() + b" ")
    with pytest.raises(SnapshotError, match="stored_receipt_mismatch"):
        runner.validate_completed_anomaly_fit(root, receipt)
