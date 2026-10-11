"""Controlled journal/budget tests; these mocked witnesses are not native truth evidence."""

import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_ai09_campaign_journal import development, selection
from test_anomaly_detectors import scope
from test_campaign_generation import campaign
from test_physical_forecast import source as declared_source

from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_anomaly_truth as runner
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_anomaly_truth_contract import (
    CampaignOrdinaryTruthPlan,
    OrdinarySourceVerification,
)
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_export import _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.source_snapshot.files import SnapshotError


def case(tmp_path, *, final=False, frozen=False):
    initial = tmp_path / "initial"
    initial.mkdir()
    previous, _, generation_id, generation_plan = campaign(initial, final=final)
    root = tmp_path.resolve() / "truth-journal"
    plan = CampaignOrdinaryTruthPlan(
        phase="final" if final else "development",
        source_recipe_sha256=generation_plan.source_recipe_sha256,
        generation_operation_id=generation_id,
        window=Window(start=date(2026, 7, 1), end=date(2026, 7, 3)),
        resources=CampaignGenerationResources(
            wall_seconds=60,
            tree_rss_bytes=256 * 1024**2,
            scratch_bytes=64 * 1024**2,
            minimum_available_memory_bytes=1024**3,
            minimum_free_disk_bytes=6 * 1024**3,
        ),
    )
    document = journal.inspect(previous).protocol.model_dump(mode="json")
    document["journal_path"] = str(root)
    read_id = ("final" if final else "development") + "-42-read"
    next(o for o in document["operations"] if o["operation_id"] == read_id)[
        "execution_recipe_sha256"
    ] = plan.content_sha256()
    protocol = CampaignProtocol.model_validate_json(canonical_bytes(document))
    journal.initialize(root, protocol)
    if final and frozen:
        development(root)
        journal.freeze_selection(root, selection(root))
    source = declared_source().model_copy(
        update={"source_parameters": generation_plan.resolved_parameters}
    )
    # Final access is intentionally left unreserved to exercise the actual guard.
    reservation_id = (
        "campaign-operation-" + "0" * 32
        if final and not frozen
        else str(journal.reserve(root, generation_id).reservation_id)
    )
    generated = CampaignGeneratedParentReceipt(
        protocol_sha256=protocol.content_sha256(),
        source_recipe_sha256=plan.source_recipe_sha256,
        operation_id=generation_id,
        reservation_id=reservation_id,
        source=source,
        runtime=protocol.runtime,
    )
    if not final or frozen:
        _store_receipt(root, generated)
        journal.finish(
            root,
            reservation_id,
            result="completed",
            evidence_sha256=generated.content_sha256(),
            cost=CampaignCost(wall_seconds=0.01),
        )
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    return root, output, read_id, plan, generated, generation_plan


def execute(value, **changes):
    root, output, operation, plan, generated, generation = value
    return runner.read_campaign_ordinary_truth(
        output,
        output,
        Path(sys.executable),
        output,
        journal=root,
        operation_id=operation,
        plan=changes.get("plan", plan),
        generated=changes.get("generated", generated),
        generation_plan=changes.get("generation", generation),
        selection_bundles=changes.get("selection_bundles"),
    )


def controlled(monkeypatch, value, *, failure=None):
    root, _, operation, plan, generated, generation = value
    monkeypatch.setattr(runner, "_producer_pin", lambda *a: None)
    calls = []

    def monitor(*args, **kwargs):
        latest = journal.inspect(root).events[-1]
        assert latest.kind == "reserved" and latest.operation_id == operation
        calls.append(operation)
        if failure == "worker":
            return {
                "status": "failed",
                "reason": "worker_exit",
                "wall_seconds": 0.1,
                "sampled_tree_peak_rss_bytes": 12345,
            }
        folder = kwargs["root"]
        request = runner.read(folder / "request.json")
        windows = [
            {
                **scope().model_dump(mode="json"),
                "window": plan.window.model_dump(mode="json"),
                "available_at": "2026-07-05T00:00:00Z",
            }
        ]
        witness = OrdinarySourceVerification(
            source_dataset_id=generated.source.parent.source_dataset_id,
            source_descriptor_sha256=generated.source.parent.source_dataset_id.removeprefix(
                "source-sha256-"
            ),
            source_manifest_sha256="1" * 64,
            source_report_sha256="2" * 64,
            source_table_inventory_sha256="3" * 64,
            source_tables=1,
            source_rows=3,
            producer_commit=request["producer_commit"],
            producer_code_sha256="4" * 64,
            producer_lock_sha256=request["producer_lock_sha256"],
            exporter_lock_sha256=request["exporter_lock_sha256"],
            producer_python_version="3.11.15",
            resolved_parameters=request["resolved_parameters"],
            window=plan.window,
            complete_windows_sha256=canonical_sha256(windows),
            complete_window_count=1,
            complete_observation_count=3,
        )
        if failure == "binding":
            witness = witness.model_copy(update={"producer_commit": "f" * 40})
        if failure == "census":
            windows = []
        write(folder / "ordinary-source-verification.json", witness.model_dump(mode="json"))
        write(folder / "ordinary-source-windows.json", {"complete_windows": windows})
        write(
            folder / "truth-worker-resources.json",
            {
                "worker_peak_rss_bytes": 2 * 1024**3 if failure == "rss" else 10000,
                "wall_seconds": 0.09,
                "cpu_seconds": 0.07,
            },
        )
        return {"status": "passed", "wall_seconds": 0.1, "sampled_tree_peak_rss_bytes": 12345}

    monkeypatch.setattr(runner, "monitor", monitor)
    return calls


@pytest.mark.parametrize("change", ["plan", "generation", "receipt", "missing_receipt"])
def test_binding_failure_consumes_one_read_without_source_io(tmp_path, monkeypatch, change):
    value = case(tmp_path)
    root, _, _, plan, generated, generation = value

    def forbidden(*a, **k):
        pytest.fail("Source read occurred before complete ancestry verification")

    monkeypatch.setattr(runner, "monitor", forbidden)
    monkeypatch.setattr(runner, "_producer_pin", forbidden)
    changes = {}
    if change == "plan":
        changes["plan"] = plan.model_copy(update={"source_recipe_sha256": "0" * 64})
    elif change == "generation":
        changes["generation"] = generation.model_copy(
            update={"entrypoint": "planned_anomaly", "scenario_plan": {"unknown": True}}
        )
    elif change == "receipt":
        changes["generated"] = generated.model_copy(update={"protocol_sha256": "0" * 64})
    else:
        (root / "receipts" / (generated.reservation_id + ".json")).unlink()
    with pytest.raises((ValueError, OSError)):
        execute(value, **changes)
    events = journal.inspect(root).events
    assert events[-2].kind == "reserved" and events[-1].result == "failed"
    assert events[-1].cost.wall_seconds > 0
    with pytest.raises(ValidationError, match="budget_exhausted"):
        execute(value)


@pytest.mark.parametrize("failure", ["worker", "binding", "census", "rss"])
def test_incomplete_native_verification_never_creates_completed_truth(
    tmp_path, monkeypatch, failure
):
    value = case(tmp_path)
    calls = controlled(monkeypatch, value, failure=failure)
    with pytest.raises(ValueError):
        execute(value)
    ledger = journal.inspect(value[0])
    assert ledger.events[-1].result == "failed"
    assert calls == [value[2]]
    assert not (value[0] / "receipts" / (str(ledger.events[-1].reservation_id) + ".json")).exists()


def test_truth_bundle_is_durable_bound_and_nonrepeatable(tmp_path, monkeypatch):
    value = case(tmp_path)
    controlled(monkeypatch, value)
    bundle, receipt = execute(value)
    truth = runner.verify_campaign_ordinary_truth(bundle, journal=value[0], receipt=receipt)
    assert truth.source_scenario_sha256 is None and not truth.episodes
    assert truth.source_generation_receipt_sha256 == value[4].content_sha256()
    assert journal.inspect(value[0]).events[-1].evidence_sha256 == receipt.content_sha256()
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in bundle.iterdir())
    with pytest.raises(ValidationError, match="budget_exhausted"):
        execute(value)
    (bundle / "truth.json").write_text("{}")
    with pytest.raises(SnapshotError, match="bundle_inventory"):
        runner.verify_campaign_ordinary_truth(bundle, journal=value[0], receipt=receipt)


def test_final_truth_cannot_be_read_before_selection(tmp_path, monkeypatch):
    value = case(tmp_path, final=True)

    def forbidden(*a, **k):
        pytest.fail("final Source I/O before selection")

    monkeypatch.setattr(runner, "_producer_pin", forbidden)
    with pytest.raises(ValueError):
        execute(value)
    assert not journal.inspect(value[0]).events


@pytest.mark.parametrize("bundles", [None, {}, {"forecast": "missing"}, "three_paths"])
def test_final_metadata_freeze_cannot_replace_completed_three_use_proof(
    tmp_path, monkeypatch, bundles
):
    value = case(tmp_path, final=True, frozen=True)

    def forbidden(*a, **k):
        pytest.fail("final Source I/O before completed three-use selection replay")

    monkeypatch.setattr(runner, "_producer_pin", forbidden)
    if bundles == "three_paths":
        bundles = {use: tmp_path / use for use in ("forecast", "anomaly", "stockout")}
    with pytest.raises(SnapshotError, match="selection|no_completed_use_evaluation"):
        execute(value, selection_bundles=bundles)
    event = journal.inspect(value[0]).events[-1]
    assert event.result == "failed" and event.cost.wall_seconds > 0
