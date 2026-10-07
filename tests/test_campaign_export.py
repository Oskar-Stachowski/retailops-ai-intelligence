"""Controlled metadata proves audit binding; mocked data is not native acceptance."""

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_ai09_campaign_journal import protocol_document
from test_physical_forecast import source as declared_source

from retailops_ai.data_contracts.common import DateWindow
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_export as exporter
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, CampaignProtocol
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportPlan,
    CampaignDevelopmentExportReceipt,
    CampaignGeneratedParentReceipt,
)
from retailops_ai.evaluation_campaign.partitions import chronological_policy
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.source_snapshot.files import SnapshotError


def setup_campaign(tmp_path, *, generated_change=None, generation_result="completed"):
    root = tmp_path.resolve() / "journal"
    document = protocol_document(root)
    s = document["sources"][0]
    parameters = {
        "profile": "ai-dev",
        "seed": 42,
        "days": 365,
        "products": 100,
        "stores": 5,
        "warehouses": 3,
        "start_date": s["history"]["start"],
        "end_date": s["history"]["end"],
        "business_timezone": "UTC",
        "output_format": "csv",
        "warmup_days": 0,
        "origin_days": 0,
        "label_tail_days": 0,
        "max_daily_rows": 182500,
    }
    old = canonical_sha256(s)
    s["generation_config_sha256"] = canonical_sha256(parameters)
    source_digest = canonical_sha256(s)
    for operation in document["operations"]:
        if operation["source_recipe_sha256"] == old:
            operation["source_recipe_sha256"] = source_digest
    temporal = chronological_policy(
        DateWindow(start=date(2026, 3, 9), end=date(2026, 7, 16)),
        train_days=30,
        other_role_days=10,
    )
    plan = CampaignDevelopmentExportPlan(
        source_recipe_sha256=source_digest,
        generation_operation_id="development-42-generate",
        origins=OriginWindow(
            start=temporal.roles[0].origins.start, end=temporal.roles[-1].origins.end
        ),
        roles=temporal.roles,
    )
    next(o for o in document["operations"] if o["operation_id"] == "development-42-read")[
        "execution_recipe_sha256"
    ] = plan.content_sha256()
    protocol = CampaignProtocol.model_validate_json(canonical_bytes(document))
    journal.initialize(root, protocol)
    spec = declared_source().model_dump(mode="json")
    spec["source_parameters"] = parameters | (generated_change or {})
    spec = PhysicalSourceSpec.model_validate_json(canonical_bytes(spec))
    start = journal.reserve(root, "development-42-generate")
    generated = CampaignGeneratedParentReceipt(
        protocol_sha256=protocol.content_sha256(),
        source_recipe_sha256=source_digest,
        operation_id="development-42-generate",
        reservation_id=str(start.reservation_id),
        source=spec,
        runtime=protocol.runtime,
    )
    journal.finish(
        root,
        str(start.reservation_id),
        result=generation_result,
        evidence_sha256=generated.content_sha256() if generation_result == "completed" else None,
        cost=CampaignCost(wall_seconds=0.01),
        error_code="controlled_generation_failure" if generation_result == "failed" else None,
    )
    return root, protocol, plan, generated


def run(case, tmp_path):
    root, _, plan, generated = case
    return exporter.export_development_forecast(
        tmp_path / "never_existing_snapshot",
        tmp_path / "never_existing_curated",
        tmp_path / "output",
        journal=root,
        operation_id="development-42-read",
        plan=plan,
        generated=generated,
    )


def assert_failed_read(root):
    events = journal.inspect(root).events
    assert events[-2].kind == "reserved" and events[-2].operation_id == "development-42-read"
    assert events[-1].kind == "finished" and events[-1].result == "failed"
    assert events[-1].cost is not None and events[-1].cost.wall_seconds > 0
    assert events[-1].cost.peak_process_tree_rss_bytes is None
    assert (
        journal.summary(root)["remaining_new_attempts"]
        == journal.inspect(root).protocol.maximum_new_attempts - 2
    )
    assert not (root / "receipts").exists()


def forbid_parent(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("parent I/O happened before frozen binding checks")

    monkeypatch.setattr(exporter, "_open_verified_source_parent", forbidden)


@pytest.mark.parametrize(
    "field,value",
    [
        ("products", 99),
        ("seed", 137),
        ("profile", "ai-load"),
        ("end_date", "2026-08-01"),
        ("max_daily_rows", 182501),
    ],
)
def test_generation_receipt_with_nonfrozen_config_never_reads_parent(
    tmp_path, monkeypatch, field, value
):
    case = setup_campaign(tmp_path, generated_change={field: value})
    forbid_parent(monkeypatch)
    with pytest.raises(SnapshotError, match="canonical_source"):
        run(case, tmp_path)
    assert_failed_read(case[0])


@pytest.mark.parametrize(
    "change", ["plan", "receipt_digest", "protocol", "reservation", "runtime", "parent_budget"]
)
def test_changed_plan_or_generation_never_reads_parent_and_stays_charged(
    tmp_path, monkeypatch, change
):
    root, protocol, plan, generated = setup_campaign(tmp_path)
    if change == "plan":
        plan = plan.model_copy(update={"max_index_bytes": plan.max_index_bytes + 4096})
    else:
        value = generated.model_dump(mode="json")
        if change == "protocol":
            value["protocol_sha256"] = "f" * 64
        elif change == "reservation":
            value["reservation_id"] = "campaign-operation-" + "f" * 32
        elif change == "runtime":
            value["runtime"]["python_version"] = "3.11.14"
        elif change == "parent_budget":
            value["source"]["batch_rows"] = 1
        else:
            value["source"]["snapshot_manifest_sha256"] = "f" * 64
        generated = CampaignGeneratedParentReceipt.model_validate_json(canonical_bytes(value))
    forbid_parent(monkeypatch)
    with pytest.raises(SnapshotError):
        run((root, protocol, plan, generated), tmp_path)
    assert_failed_read(root)
    with pytest.raises(ValidationError, match="budget_exhausted"):
        run((root, protocol, plan, generated), tmp_path)


def test_failed_generation_prevents_new_read_reservation(tmp_path, monkeypatch):
    case = setup_campaign(tmp_path, generation_result="failed")
    forbid_parent(monkeypatch)
    with pytest.raises(ValidationError, match="prerequisites_not_completed"):
        run(case, tmp_path)
    assert len(journal.inspect(case[0]).events) == 2


@pytest.mark.parametrize("operation", ["development-fit", "final-42-read", "unknown"])
def test_wrong_operation_never_consumes_an_unrelated_budget(tmp_path, monkeypatch, operation):
    root, _, plan, generated = setup_campaign(tmp_path)
    forbid_parent(monkeypatch)
    with pytest.raises(SnapshotError, match="development_parent_read"):
        exporter.export_development_forecast(
            tmp_path,
            tmp_path,
            tmp_path,
            journal=root,
            operation_id=operation,
            plan=plan,
            generated=generated,
        )
    assert len(journal.inspect(root).events) == 2


def mocked_pipeline(monkeypatch, case, *, failure=None, producer_change=None):
    root, protocol, plan, generated = case
    observed = []
    expected = protocol.sources[0]
    scalars = {
        "producer_commit": expected.producer_commit,
        "producer_code_state": "clean",
        "producer_lock_sha256": expected.producer_lock_sha256,
        "exporter_commit": expected.producer_commit,
        "exporter_lock_sha256": canonical_sha256("controlled-parquet-lock"),
        "declared_exporter_lock_sha256": canonical_sha256("controlled-parquet-lock"),
    } | (producer_change or {})

    def check():
        event = journal.inspect(root).events[-1]
        assert event.kind == "reserved" and event.operation_id == "development-42-read"
        observed.append("active_audit")

    replay = SimpleNamespace(
        snapshot_inventory_sha256=canonical_sha256("controlled-snapshot-inventory"),
        curated_inventory_sha256=canonical_sha256("controlled-curated-inventory"),
        logical_curated_sha256=canonical_sha256("controlled-logical-curated"),
        check_parents=check,
        **scalars,
    )

    @contextmanager
    def open_parent(*args, **kwargs):
        check()
        if failure == "open":
            raise SnapshotError("controlled_open_failure")
        yield replay
        check()
        if failure == "context_exit":
            raise SnapshotError("controlled_parent_changed_on_exit")

    destination = []

    def build(parent, recipe, output):
        check()
        assert parent is replay and recipe == plan.bind(generated.source)
        if failure == "build":
            raise SnapshotError("controlled_build_failure")
        path = output / ("ai09-physical-forecast-sha256-" + "a" * 64)
        path.mkdir(parents=True, mode=0o700)
        (path / "manifest.json").write_bytes(
            canonical_bytes({"scope": "controlled_mock_not_native"}) + b"\n"
        )
        destination.append(path)
        return path

    def verify(path):
        check()
        assert path == destination[0]
        if failure == "verify":
            raise SnapshotError("controlled_verification_failure")
        return SimpleNamespace(
            dataset_id=path.name,
            descriptor=SimpleNamespace(
                recipe=plan.bind(generated.source),
                runtime=protocol.runtime,
                snapshot_inventory_sha256=replay.snapshot_inventory_sha256,
                curated_inventory_sha256=replay.curated_inventory_sha256,
                logical_curated_sha256=replay.logical_curated_sha256,
                feature_descriptor=SimpleNamespace(row_count=75),
            ),
        )

    monkeypatch.setattr(exporter, "_open_verified_source_parent", open_parent)
    monkeypatch.setattr(exporter, "_build_physical_forecast", build)
    monkeypatch.setattr(exporter, "verify_physical_forecast", verify)
    return observed


def test_complete_receipt_is_durable_and_bound_to_actual_finished_event(tmp_path, monkeypatch):
    case = setup_campaign(tmp_path)
    observed = mocked_pipeline(monkeypatch, case)
    destination, receipt = run(case, tmp_path)
    assert len(observed) >= 5 and destination.exists()
    exporter.validate_completed_export(case[0], receipt)
    event = journal.inspect(case[0]).events[-1]
    assert event.result == "completed" and event.evidence_sha256 == receipt.content_sha256()
    assert event.cost.artifact_bytes == receipt.artifact_bytes
    assert event.cost.peak_process_tree_rss_bytes is None
    assert (
        not receipt.resource_qualified
        and not receipt.holdout_freshness_qualified
        and not receipt.stage_ready
    )
    stored = case[0] / "receipts" / (receipt.reservation_id + ".json")
    assert stored.stat().st_mode & 0o777 == 0o600
    assert stored.read_bytes() == canonical_bytes(receipt.model_dump(mode="json")) + b"\n"


@pytest.mark.parametrize("failure", ["open", "build", "verify", "context_exit", "receipt_publish"])
def test_any_pipeline_failure_is_charged_and_cannot_create_completion(
    tmp_path, monkeypatch, failure
):
    case = setup_campaign(tmp_path)
    mocked_pipeline(monkeypatch, case, failure=failure)
    if failure == "receipt_publish":

        def fail(*args):
            raise OSError("controlled receipt publication error")

        monkeypatch.setattr(exporter, "_store_receipt", fail)
    with pytest.raises((SnapshotError, OSError)):
        run(case, tmp_path)
    assert_failed_read(case[0])


@pytest.mark.parametrize(
    "field,value",
    [
        ("producer_commit", "f" * 40),
        ("producer_code_state", "dirty"),
        ("producer_lock_sha256", "f" * 64),
        ("exporter_commit", "f" * 40),
        ("exporter_lock_sha256", "f" * 64),
        ("declared_exporter_lock_sha256", None),
    ],
)
def test_verified_producer_mismatch_cannot_reach_builder(tmp_path, monkeypatch, field, value):
    case = setup_campaign(tmp_path)
    observed = mocked_pipeline(monkeypatch, case, producer_change={field: value})
    with pytest.raises(SnapshotError, match="producer_or_lock"):
        run(case, tmp_path)
    assert len(observed) == 1
    assert_failed_read(case[0])


@pytest.mark.parametrize(
    "change", ["declared_result", "stored_bytes", "stored_symlink", "stored_mode"]
)
def test_completion_validation_rejects_unbound_or_changed_private_receipt(
    tmp_path, monkeypatch, change
):
    case = setup_campaign(tmp_path)
    mocked_pipeline(monkeypatch, case)
    _, receipt = run(case, tmp_path)
    stored = case[0] / "receipts" / (receipt.reservation_id + ".json")
    if change == "declared_result":
        receipt = receipt.model_copy(update={"manifest_sha256": "f" * 64})
    elif change == "stored_bytes":
        stored.write_bytes(stored.read_bytes() + b" ")
    elif change == "stored_mode":
        stored.chmod(0o644)
    else:
        raw = stored.read_bytes()
        stored.unlink()
        target = tmp_path / "other-receipt"
        target.write_bytes(raw)
        stored.symlink_to(target)
    with pytest.raises(SnapshotError):
        exporter.validate_completed_export(case[0], receipt)


def test_recipe_cannot_grant_final_or_resources_and_budgets_cannot_change(tmp_path):
    _, _, plan, generated = setup_campaign(tmp_path)
    for field in ("resource_qualified", "final_test_access_authorized", "promotion_allowed"):
        value = plan.model_dump(mode="json") | {field: True}
        with pytest.raises(ValidationError):
            CampaignDevelopmentExportPlan.model_validate_json(canonical_bytes(value))
    with pytest.raises(ValueError, match="parent_budget_changed"):
        plan.bind(generated.source.model_copy(update={"batch_rows": 1}))
    value = plan.model_dump(mode="json") | {"phase": "final"}
    with pytest.raises(ValidationError):
        CampaignDevelopmentExportPlan.model_validate_json(canonical_bytes(value))


def test_public_models_have_strict_json_schemas(tmp_path):
    _, _, plan, generated = setup_campaign(tmp_path)
    for model, value in (
        (CampaignDevelopmentExportPlan, plan),
        (CampaignGeneratedParentReceipt, generated),
    ):
        assert model.model_json_schema()["additionalProperties"] is False
        assert model.model_validate_json(value.model_dump_json()) == value
    assert CampaignDevelopmentExportReceipt.model_json_schema()["additionalProperties"] is False
