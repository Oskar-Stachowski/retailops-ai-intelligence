"""Durable reservation/binding tests with explicit worker and cost doubles."""

import hashlib
from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_campaign_role_protocol import role_document
from test_physical_forecast import source as declared_source

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import campaign_stockout_preparation as component
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_export import _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_role_protocol import RoleAwareCampaignProtocol
from retailops_ai.evaluation_campaign.campaign_stockout_maturity_contract import (
    CampaignStockoutMaturityPolicy,
)
from retailops_ai.evaluation_campaign.campaign_stockout_preparation_contract import (
    CampaignStockoutPreparationPlan,
)
from retailops_ai.evaluation_campaign.campaign_stockout_role_contract import FILES
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.source_snapshot.files import SnapshotError


def limits():
    return CampaignGenerationResources(
        wall_seconds=600,
        tree_rss_bytes=256 * 1024**2,
        scratch_bytes=8 * 1024**3,
        minimum_free_disk_bytes=6 * 1024**3,
        minimum_available_memory_bytes=1024**3,
    )


def setup(tmp_path, *, generation_result="completed"):
    root = tmp_path / "journal"
    document = role_document(root)
    source = document["sources"][0]
    parameters = {
        "profile": "ai-dev",
        "seed": 42,
        "days": 365,
        "products": 100,
        "stores": 5,
        "warehouses": 3,
        "start_date": source["history"]["start"],
        "end_date": source["history"]["end"],
        "business_timezone": "UTC",
    }
    replacements = {}
    for variant in document["sources"][:3]:
        old = canonical_sha256(variant)
        variant["generation_config_sha256"] = canonical_sha256(parameters)
        replacements[old] = canonical_sha256(variant)
    digest = canonical_sha256(source)
    for operation in document["operations"]:
        operation["source_recipe_sha256"] = replacements.get(
            operation["source_recipe_sha256"], operation["source_recipe_sha256"]
        )
    document["training_source_recipe_sha256"] = {
        case: replacements[value]
        for case, value in document["training_source_recipe_sha256"].items()
    }
    gen_id, read_id = "development-42-ordinary-generate", "development-42-ordinary-read"
    plan = CampaignStockoutPreparationPlan(
        source_recipe_sha256=digest,
        generation_operation_id=gen_id,
        policy=CampaignStockoutMaturityPolicy.model_validate_json(
            canonical_bytes({"windows": document["development_roles"]["windows"]})
        ),
        resources=limits(),
    )
    next(o for o in document["operations"] if o["operation_id"] == read_id)[
        "execution_recipe_sha256"
    ] = plan.content_sha256()
    protocol = RoleAwareCampaignProtocol.model_validate_json(canonical_bytes(document))
    journal.initialize(root, protocol)
    reservation = journal.reserve(root, gen_id)
    specification = declared_source().model_dump(mode="python")
    specification["source_parameters"] = parameters
    generated = CampaignGeneratedParentReceipt(
        protocol_sha256=protocol.content_sha256(),
        source_recipe_sha256=digest,
        operation_id=gen_id,
        reservation_id=str(reservation.reservation_id),
        source=PhysicalSourceSpec.model_validate(specification),
        runtime=protocol.runtime,
    )
    # Test-only metadata generation evidence, never a claim of real Source execution.
    if generation_result == "completed":
        _store_receipt(root, generated)
    journal.finish(
        root,
        str(reservation.reservation_id),
        result=generation_result,
        evidence_sha256=generated.content_sha256() if generation_result == "completed" else None,
        cost=CampaignCost(wall_seconds=0.01),
        error_code="controlled_generation_failure" if generation_result == "failed" else None,
    )
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    return dict(journal=root, operation_id=read_id, plan=plan, generated=generated), output


def invoke(case, output):
    return component.prepare_campaign_stockout(
        output / "absent-snapshot",
        output / "absent-curated",
        output / "absent-qualification",
        output,
        **case,
    )


def worker_double(monkeypatch, *, failure=None, worker_peak=8192):
    phases = []

    def monitor(command, *, root, **kwargs):
        phase = command[-2]
        request = read(root / "request.json")
        plan = CampaignStockoutPreparationPlan.model_validate_json(canonical_bytes(request["plan"]))
        generated = CampaignGeneratedParentReceipt.model_validate_json(
            canonical_bytes(request["generated"])
        )
        ledger = journal.inspect(root.parents[1] / "journal")
        assert ledger.events[-1].kind == "reserved"
        assert not (ledger.protocol.journal_path == str(root))
        phases.append(phase)
        measured = dict(
            status="failed" if phase == failure else "passed",
            sampled_tree_peak_rss_bytes=4096,
            reason="controlled",
            exit_code=1 if phase == failure else 0,
        )
        if phase == failure:
            return measured
        result = dict(worker_peak_rss_bytes=worker_peak, worker_wall_seconds=0.001)
        if phase == "verify":
            bundle = root / "bundle"
            bundle.mkdir(mode=0o700)
            for name in {"manifest.json", *FILES}:
                (bundle / name).write_bytes(b"controlled worker double\n")
                (bundle / name).chmod(0o600)
            files, size = component._bundle_inventory(bundle, plan.max_role_bytes)
            public_id = "ai09-stockout-public-sha256-" + "a" * 64
            result.update(
                public_dataset_id=public_id,
                dataset_id="ai09-stockout-roles-sha256-" + "b" * 64,
                role_plan=plan.role_plan(generated, public_id).model_dump(mode="json"),
                manifest_sha256=files["manifest.json"],
                artifact_bytes=size,
                artifact_files=files,
            )
        write(root / (phase + ".json"), result)
        return measured

    monkeypatch.setattr(component, "monitor", monitor)
    return phases


def test_complete_ordered_attempt_has_durable_receipt_and_no_retry(tmp_path, monkeypatch):
    case, output = setup(tmp_path)
    phases = worker_double(monkeypatch)
    bundle, receipt = invoke(case, output)
    assert phases == ["public", "roles", "verify"]
    component.validate_completed_stockout_preparation(case["journal"], receipt)
    assert (
        hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
        == receipt.manifest_sha256
    )
    event = journal.inspect(case["journal"]).events[-1]
    assert event.result == "completed" and event.evidence_sha256 == receipt.content_sha256()
    assert event.cost.artifact_bytes == receipt.artifact_bytes
    assert not receipt.quality_qualified and not receipt.stage_ready
    with pytest.raises((SnapshotError, ValueError)):
        invoke(case, output)
    assert phases == ["public", "roles", "verify"]


@pytest.mark.parametrize("phase", ["public", "roles", "verify"])
def test_each_failed_phase_remains_charged_without_completion(tmp_path, monkeypatch, phase):
    case, output = setup(tmp_path)
    phases = worker_double(monkeypatch, failure=phase)
    with pytest.raises(SnapshotError, match="phase_failed_" + phase):
        invoke(case, output)
    event = journal.inspect(case["journal"]).events[-1]
    assert event.result == "failed" and event.evidence_sha256 is None
    assert event.cost.wall_seconds > 0
    assert event.cost.peak_process_tree_rss_bytes == (4096 if phase == "public" else 8192)
    assert phases[-1] == phase
    with pytest.raises((SnapshotError, ValueError)):
        invoke(case, output)


@pytest.mark.parametrize(
    "change",
    ["plan", "generation", "stored_generation", "source_config", "runtime", "parent_budget"],
)
def test_invalid_binding_is_charged_before_worker_or_parent_io(tmp_path, monkeypatch, change):
    case, output = setup(tmp_path)
    if change == "plan":
        case["plan"] = case["plan"].model_copy(update={"max_rows": 100})
    elif change == "generation":
        case["generated"] = case["generated"].model_copy(update={"source_recipe_sha256": "c" * 64})
    elif change == "stored_generation":
        path = case["journal"] / "receipts" / (case["generated"].reservation_id + ".json")
        path.write_bytes(path.read_bytes() + b" ")
    else:
        value = case["generated"].model_dump(mode="python")
        if change == "runtime":
            value["runtime"]["python_version"] = "3.11.14"
        elif change == "parent_budget":
            value["source"]["max_parent_bytes"] -= 1
        else:
            value["source"]["source_parameters"]["products"] = 99
        case["generated"] = CampaignGeneratedParentReceipt.model_validate(value)
    monkeypatch.setattr(
        component, "monitor", lambda *a, **k: pytest.fail("invalid input reached worker")
    )
    with pytest.raises((SnapshotError, ValueError)):
        invoke(case, output)
    event = journal.inspect(case["journal"]).events[-1]
    assert event.result == "failed" and event.cost.peak_process_tree_rss_bytes is None
    assert not list(output.iterdir())


def test_failed_generation_cannot_reserve_read(tmp_path, monkeypatch):
    case, output = setup(tmp_path, generation_result="failed")
    before = journal.inspect(case["journal"])
    monkeypatch.setattr(component, "monitor", lambda *a, **k: pytest.fail("worker started"))
    with pytest.raises((SnapshotError, ValueError)):
        invoke(case, output)
    assert journal.inspect(case["journal"]) == before


def test_receipt_file_and_model_copies_cannot_bypass_completion(tmp_path, monkeypatch):
    case, output = setup(tmp_path)
    worker_double(monkeypatch)
    _, receipt = invoke(case, output)
    changed = deepcopy(receipt)
    changed.artifact_files.pop("base_train.jsonl")
    with pytest.raises(ValidationError):
        changed.content_sha256()
    path = case["journal"] / "receipts" / (receipt.reservation_id + ".json")
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(SnapshotError, match="stored_receipt_mismatch"):
        component.validate_completed_stockout_preparation(case["journal"], receipt)


def test_preflight_refusal_keeps_unmeasured_memory_unknown(tmp_path, monkeypatch):
    case, output = setup(tmp_path)
    phases = []

    def refuse(command, **kwargs):
        phases.append(command[-2])
        return dict(status="failed", reason="preflight_reserve", sampled_tree_peak_rss_bytes=None)

    monkeypatch.setattr(component, "monitor", refuse)
    with pytest.raises(SnapshotError, match="phase_failed_public"):
        invoke(case, output)
    event = journal.inspect(case["journal"]).events[-1]
    assert event.result == "failed" and event.evidence_sha256 is None
    assert event.cost.wall_seconds > 0 and event.cost.peak_process_tree_rss_bytes is None
    assert phases == ["public"] and not list((case["journal"] / "receipts").glob("*.tmp"))


@pytest.mark.parametrize("peak", [None, True, 0, -1, 256 * 1024**2 + 1])
def test_missing_or_excess_worker_peak_cannot_complete(tmp_path, monkeypatch, peak):
    case, output = setup(tmp_path)
    phases = worker_double(monkeypatch, worker_peak=peak)
    with pytest.raises(SnapshotError, match="worker_(resource_evidence_missing|peak_rss_limit)"):
        invoke(case, output)
    event = journal.inspect(case["journal"]).events[-1]
    assert event.result == "failed" and event.evidence_sha256 is None
    assert phases == ["public"]


def test_artifact_changed_after_worker_verification_cannot_complete(tmp_path, monkeypatch):
    case, output = setup(tmp_path)
    phases = worker_double(monkeypatch)
    original = component.monitor

    def mutate(command, *, root, **kwargs):
        result = original(command, root=root, **kwargs)
        if command[-2] == "verify":
            (root / "bundle" / "base_train.jsonl").write_bytes(b"changed after verification\n")
        return result

    monkeypatch.setattr(component, "monitor", mutate)
    with pytest.raises(SnapshotError, match="artifact_changed_after_verification"):
        invoke(case, output)
    event = journal.inspect(case["journal"]).events[-1]
    assert event.result == "failed" and event.evidence_sha256 is None
    assert not (case["journal"] / "receipts" / (event.reservation_id + ".json")).exists()
    assert phases == ["public", "roles", "verify"]


@pytest.mark.parametrize("bound", ["wall", "scratch"])
def test_completion_budget_refuses_receipt_after_successful_workers(tmp_path, monkeypatch, bound):
    case, output = setup(tmp_path)
    worker_double(monkeypatch)
    if bound == "scratch":
        monkeypatch.setattr(
            component, "scratch_bytes", lambda roots: case["plan"].resources.scratch_bytes + 1
        )
    else:
        clock = [0.0]
        monkeypatch.setattr(component, "perf_counter", lambda: clock[0])
        original = component.fsync_tree

        def sync_then_expire(root):
            original(root)
            clock[0] = case["plan"].resources.wall_seconds + 1.0

        monkeypatch.setattr(component, "fsync_tree", sync_then_expire)
    with pytest.raises(SnapshotError, match="completion_resource_limit"):
        invoke(case, output)
    event = journal.inspect(case["journal"]).events[-1]
    assert event.result == "failed" and event.evidence_sha256 is None
    assert not (case["journal"] / "receipts" / (event.reservation_id + ".json")).exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tree_rss_bytes", 12 * 1024**3 + 1),
        ("scratch_bytes", 8 * 1024**3 + 1),
        ("wall_seconds", 10801),
        ("minimum_available_memory_bytes", 1024**3 - 1),
        ("minimum_free_disk_bytes", 6 * 1024**3 - 1),
    ],
)
def test_model_copy_cannot_bypass_frozen_resource_limits(tmp_path, field, value):
    case, _ = setup(tmp_path)
    changed = case["plan"].model_copy(
        update={"resources": case["plan"].resources.model_copy(update={field: value})}
    )
    with pytest.raises(ValidationError):
        changed.content_sha256()
