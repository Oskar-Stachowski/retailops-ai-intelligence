"""Final ordering uses controlled metadata; native smoke uses exposed public facts."""

import hashlib
import json
import shutil
from collections import Counter
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_ai09_campaign_journal import development, protocol_document, selection
from test_forecast_source_replay import physical_parents as physical_parents
from test_physical_forecast import source as declared_source

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_final_export as exporter
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_export import _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_final_contract import (
    CampaignFinalExportPlan,
    FinalForecastExample,
)
from retailops_ai.evaluation_campaign.final_forecast import (
    _build_final_forecast,
    _counts,
    verify_final_forecast,
)
from retailops_ai.evaluation_campaign.partitions import membership_key, runtime_pin
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.evaluation_campaign.physical_forecast import _role_file
from retailops_ai.evaluation_campaign.source_replay import (
    _open_verified_source_parent,
    physical_limits,
)
from retailops_ai.forecasting.contract import OriginWindow, Parent
from retailops_ai.source_snapshot.files import SnapshotError, file_hash


def setup_campaign(tmp_path, *, frozen=True):
    root = tmp_path.resolve() / "journal"
    document = protocol_document(root)
    source = document["sources"][1]
    old = canonical_sha256(source)
    parameters = {
        "profile": "ai-training",
        "seed": 42,
        "days": 730,
        "products": 200,
        "stores": 10,
        "warehouses": 4,
        "start_date": source["history"]["start"],
        "end_date": source["history"]["end"],
        "business_timezone": "UTC",
    }
    source["exporter_lock_sha256"] = canonical_sha256("controlled-exporter-lock")
    source["generation_config_sha256"] = canonical_sha256(parameters)
    digest = canonical_sha256(source)
    plan = CampaignFinalExportPlan(
        source_recipe_sha256=digest,
        generation_operation_id="final-42-generate",
        origins=OriginWindow.model_validate_json(canonical_bytes(source["evaluation_origins"])),
        label_knowledge_cutoff=end_of_day(date(2026, 9, 30)),
        prior_exposure_end=date(2026, 7, 31),
    )
    for operation in document["operations"]:
        if operation["source_recipe_sha256"] == old:
            operation["source_recipe_sha256"] = digest
        if operation["operation_id"] == "final-42-read":
            operation["execution_recipe_sha256"] = plan.content_sha256()
    protocol = CampaignProtocol.model_validate_json(canonical_bytes(document))
    journal.initialize(root, protocol)
    development(root)
    if frozen:
        journal.freeze_selection(root, selection(root))
        reservation = journal.reserve(root, "final-42-generate")
        generated = CampaignGeneratedParentReceipt(
            protocol_sha256=protocol.content_sha256(),
            source_recipe_sha256=digest,
            operation_id="final-42-generate",
            reservation_id=str(reservation.reservation_id),
            source=declared_source().model_copy(update={"source_parameters": parameters}),
            runtime=protocol.runtime,
        )
        _store_receipt(root, generated)
        journal.finish(
            root,
            str(reservation.reservation_id),
            result="completed",
            evidence_sha256=generated.content_sha256(),
            cost=CampaignCost(wall_seconds=0.01),
        )
    else:
        generated = CampaignGeneratedParentReceipt(
            protocol_sha256=protocol.content_sha256(),
            source_recipe_sha256=digest,
            operation_id="final-42-generate",
            reservation_id="campaign-operation-" + "0" * 32,
            source=declared_source().model_copy(update={"source_parameters": parameters}),
            runtime=protocol.runtime,
        )
    return root, plan, generated


def run(case, tmp_path, **changes):
    root, plan, generated = case
    return exporter.export_final_forecast(
        tmp_path / "unread-snapshot",
        tmp_path / "unread-curated",
        tmp_path / "output",
        journal=root,
        operation_id=changes.pop("operation_id", "final-42-read"),
        plan=changes.pop("plan", plan),
        generated=changes.pop("generated", generated),
    )


def forbidden(*args, **kwargs):
    pytest.fail("final parent was opened before valid frozen binding")


def test_final_before_freeze_does_not_read_or_consume_final_attempt(tmp_path, monkeypatch):
    case = setup_campaign(tmp_path, frozen=False)
    monkeypatch.setattr(exporter, "_open_verified_source_parent", forbidden)
    before = journal.inspect(case[0]).head_sha256
    with pytest.raises((ValidationError, SnapshotError), match="phase_mismatch"):
        run(case, tmp_path)
    assert journal.inspect(case[0]).head_sha256 == before


@pytest.mark.parametrize(
    "operation", ["development-42-read", "final-42-score-forecast", "final-42-generate"]
)
def test_wrong_operation_has_no_parent_read_or_charge(tmp_path, monkeypatch, operation):
    case = setup_campaign(tmp_path)
    monkeypatch.setattr(exporter, "_open_verified_source_parent", forbidden)
    before = journal.inspect(case[0]).head_sha256
    with pytest.raises(SnapshotError, match="requires_final_parent_read"):
        run(case, tmp_path, operation_id=operation)
    assert journal.inspect(case[0]).head_sha256 == before


@pytest.mark.parametrize("change", ["plan", "parameters", "receipt_bytes", "missing_receipt"])
def test_bad_frozen_binding_is_charged_before_parent_io(tmp_path, monkeypatch, change):
    root, plan, generated = case = setup_campaign(tmp_path)
    monkeypatch.setattr(exporter, "_open_verified_source_parent", forbidden)
    if change == "plan":
        plan = plan.model_copy(update={"max_population_rows": 100})
    elif change == "parameters":
        generated = generated.model_copy(
            update={
                "source": generated.source.model_copy(
                    update={"source_parameters": generated.source.source_parameters | {"seed": 137}}
                )
            }
        )
    elif change == "receipt_bytes":
        (root / "receipts" / (generated.reservation_id + ".json")).write_bytes(b"{}\n")
    else:
        (root / "receipts" / (generated.reservation_id + ".json")).unlink()
    with pytest.raises(SnapshotError):
        run(case, tmp_path, plan=plan, generated=generated)
    assert journal.inspect(root).events[-1].result == "failed"
    assert journal.inspect(root).events[-1].cost.wall_seconds > 0
    with pytest.raises((SnapshotError, ValidationError), match="budget_exhausted"):
        run(case, tmp_path)


@pytest.mark.parametrize("change", ["past_exposure", "immature", "grant_test", "parent_budget"])
def test_final_plan_rejects_overlap_immaturity_and_self_granted_access(tmp_path, change):
    _, plan, generated = setup_campaign(tmp_path)
    value = plan.model_dump(mode="json")
    if change == "past_exposure":
        value["prior_exposure_end"] = "2026-08-01"
    elif change == "immature":
        value["label_knowledge_cutoff"] = "2026-09-29T23:59:59Z"
    elif change == "grant_test":
        value["final_test_access_authorized"] = True
    else:
        value["parent_budget"]["batch_rows"] = 128
    with pytest.raises(ValueError):
        CampaignFinalExportPlan.model_validate_json(canonical_bytes(value)).bind(generated.source)


def mocked_pipeline(monkeypatch, case, *, failure=None):
    root, plan, generated = case
    ledger = journal.inspect(root)
    source = next(
        s for s in ledger.protocol.sources if s.content_sha256() == plan.source_recipe_sha256
    )
    replay = SimpleNamespace(
        producer_commit=source.producer_commit,
        producer_code_state="clean",
        producer_lock_sha256=source.producer_lock_sha256,
        exporter_commit=source.producer_commit,
        exporter_lock_sha256=source.exporter_lock_sha256,
        snapshot_inventory_sha256="1" * 64,
        curated_inventory_sha256="2" * 64,
        logical_curated_sha256="3" * 64,
        check_parents=lambda: None,
    )
    manifest = SimpleNamespace(
        dataset_id="ai09-final-forecast-sha256-" + "4" * 64,
        descriptor=SimpleNamespace(
            recipe=plan.bind(generated.source),
            runtime=ledger.protocol.runtime,
            snapshot_inventory_sha256=replay.snapshot_inventory_sha256,
            curated_inventory_sha256=replay.curated_inventory_sha256,
            logical_curated_sha256=replay.logical_curated_sha256,
            population=SimpleNamespace(row_count=32),
        ),
    )

    @contextmanager
    def open_parent(*args, **kwargs):
        assert journal.inspect(root).events[-1].kind == "reserved"
        assert any(e.kind == "selection_frozen" for e in journal.inspect(root).events)
        if failure == "open":
            raise SnapshotError("controlled_open_failure")
        yield replay
        if failure == "exit":
            raise SnapshotError("controlled_exit_guard_failure")

    def build(_replay, _recipe, output):
        if failure == "build":
            raise SnapshotError("controlled_build_failure")
        output.mkdir()
        (output / "manifest.json").write_bytes(b"controlled_manifest\n")
        return output

    monkeypatch.setattr(exporter, "_open_verified_source_parent", open_parent)
    monkeypatch.setattr(exporter, "_build_final_forecast", build)
    monkeypatch.setattr(exporter, "verify_final_forecast", lambda _root: manifest)


def test_completed_final_receipt_is_private_durable_and_selection_bound(tmp_path, monkeypatch):
    case = setup_campaign(tmp_path)
    mocked_pipeline(monkeypatch, case)
    _, receipt = run(case, tmp_path)
    root = case[0]
    exporter.validate_completed_final_export(root, receipt)
    assert journal.inspect(root).events[-1].evidence_sha256 == receipt.content_sha256()
    assert (root / "receipts" / (receipt.reservation_id + ".json")).stat().st_mode & 0o777 == 0o600
    assert not receipt.holdout_freshness_qualified and not receipt.stage_ready
    with pytest.raises(SnapshotError, match="not_completed"):
        exporter.validate_completed_final_export(
            root, receipt.model_copy(update={"selection_sha256": "f" * 64})
        )
    (root / "receipts" / (receipt.reservation_id + ".json")).write_bytes(b"{}\n")
    with pytest.raises(SnapshotError, match="stored_receipt_mismatch"):
        exporter.validate_completed_final_export(root, receipt)


@pytest.mark.parametrize("failure", ["open", "build", "exit"])
def test_any_final_pipeline_failure_preserves_charge_and_withholds_receipt(
    tmp_path, monkeypatch, failure
):
    case = setup_campaign(tmp_path)
    mocked_pipeline(monkeypatch, case, failure=failure)
    with pytest.raises(SnapshotError, match="controlled"):
        run(case, tmp_path)
    root = case[0]
    final = journal.inspect(root).events[-1]
    assert final.result == "failed" and final.cost.wall_seconds > 0
    assert not (root / "receipts" / (str(final.reservation_id) + ".json")).exists()


def native_spec(snapshot, curated):
    metadata = json.loads((snapshot / "snapshot_manifest.json").read_bytes())
    manifest = json.loads((curated / "curated_manifest.json").read_bytes())
    return PhysicalSourceSpec(
        schema_version=metadata["schema_version"],
        parent=Parent(
            source_dataset_id=metadata["source_dataset_id"],
            snapshot_id=metadata["snapshot_id"],
            curated_dataset_id=manifest["curated_dataset_id"],
            curated_descriptor_sha256=canonical_sha256(manifest["descriptor"]),
            business_timezone="UTC",
            forecast_source_status="passed",
        ),
        source_parameters=metadata["source"]["descriptor"]["resolved_parameters"],
        snapshot_manifest_sha256=file_hash(snapshot, "snapshot_manifest.json")[1],
        curated_manifest_sha256=file_hash(curated, "curated_manifest.json")[1],
    )


def test_real_final_wire_on_exposed_smoke_has_only_final_keys_and_full_verification(
    physical_parents, tmp_path
):
    snapshot, curated = physical_parents
    spec = native_spec(snapshot, curated)
    plan = CampaignFinalExportPlan(
        source_recipe_sha256=canonical_sha256("exposed-smoke-control-not-project"),
        generation_operation_id="controlled-generation",
        origins=OriginWindow(start=date(2026, 7, 4), end=date(2026, 7, 5)),
        label_knowledge_cutoff=end_of_day(date(2026, 7, 31)),
        prior_exposure_end=date(2026, 6, 18),
    )
    runtime = runtime_pin()
    with _open_verified_source_parent(
        snapshot, curated, spec, limits=physical_limits(spec), runtime=runtime
    ) as replay:
        output = _build_final_forecast(replay, plan.bind(spec), tmp_path / "final-output")
    manifest = verify_final_forecast(output)
    assert manifest.descriptor.population.row_count > 0
    assert (
        manifest.descriptor.population.row_count == manifest.descriptor.feature_descriptor.row_count
    )
    assert not manifest.holdout_freshness_qualified and not manifest.stage_ready
    assert not any(
        (output / (role + ".jsonl")).exists()
        for role in ["train", "tune", "calibration", "development_evaluation"]
    )
    with (output / "final_evaluation.jsonl").open("rb") as stream:
        for line in stream:
            example = FinalForecastExample.model_validate_json(line)
            assert example.outcome.label.role == "final_evaluation"
            assert date(2026, 7, 4) <= example.key.forecast_origin.date() <= date(2026, 7, 5)
    # Attackers can recompute their own hashes: reject their changed records
    # against the complete independently verified feature population as well.
    for mutation in ("feature_hash", "duplicate", "dropped_key"):
        altered = tmp_path / mutation
        shutil.copytree(output, altered)
        records = [
            json.loads(line)
            for line in (altered / "final_evaluation.jsonl").read_bytes().splitlines()
        ]
        if mutation == "feature_hash":
            records[0]["outcome"]["feature_row_sha256"] = "f" * 64
        elif mutation == "duplicate":
            records[1] = records[0]
        else:
            records.pop()
        items = [FinalForecastExample.model_validate_json(canonical_bytes(row)) for row in records]
        raw = b"".join(canonical_bytes(row) + b"\n" for row in records)
        (altered / "final_evaluation.jsonl").write_bytes(raw)
        counts = Counter()
        for item in items:
            _counts(counts, item)
        keys = b"".join(membership_key(item.key) + b"\n" for item in items)
        value = manifest.model_dump(mode="json")
        value["descriptor"]["population"] = _role_file(
            counts, len(raw), hashlib.sha256(raw).hexdigest(), hashlib.sha256(keys).hexdigest()
        ).model_dump(mode="json")
        value["dataset_id"] = "ai09-final-forecast-sha256-" + canonical_sha256(value["descriptor"])
        (altered / "manifest.json").write_bytes(canonical_bytes(value) + b"\n")
        with pytest.raises((SnapshotError, ValidationError)):
            verify_final_forecast(altered)
    with (output / "final_evaluation.jsonl").open("ab") as stream:
        stream.write(b"{}\n")
    with pytest.raises(SnapshotError, match="checksum"):
        verify_final_forecast(output)
