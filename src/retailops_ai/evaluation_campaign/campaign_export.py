"""Export development facts only after a frozen, durably charged parent read."""

import os
import stat
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibrationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
    CampaignSourceRecipe,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_receipt import (
    CampaignForecastEvaluationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportPlan,
    CampaignDevelopmentExportReceipt,
    CampaignGeneratedParentReceipt,
)
from retailops_ai.evaluation_campaign.campaign_final_contract import CampaignFinalExportReceipt
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitReceipt
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScoreReceipt
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.evaluation_campaign.physical_forecast import (
    _build_physical_forecast,
    _physical_bytes,
    verify_physical_forecast,
)
from retailops_ai.evaluation_campaign.source_replay import (
    PrivateSourceReplay,
    _open_verified_source_parent,
    physical_limits,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    directory_fd,
    file_hash,
    regular_file,
)

MAX_RECEIPT_BYTES = 512 * 1024


def _operation(ledger: CampaignJournal, operation_id: str) -> CampaignOperationPlan:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or (
        operation.phase != "development"
        or operation.action != "source_read"
        or operation.use_case != "source"
        or operation.role != "all_parent_data"
    ):
        raise SnapshotError("campaign_export_requires_development_parent_read")
    return operation


def _binding(
    ledger: CampaignJournal,
    operation: CampaignOperationPlan,
    plan: CampaignDevelopmentExportPlan,
    generated: CampaignGeneratedParentReceipt,
) -> CampaignSourceRecipe:
    if (
        operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or generated.source_recipe_sha256 != plan.source_recipe_sha256
        or generated.protocol_sha256 != ledger.protocol_sha256
        or generated.runtime != ledger.protocol.runtime
        or generated.operation_id != plan.generation_operation_id
        or plan.generation_operation_id not in operation.prerequisites
    ):
        raise SnapshotError("campaign_export_frozen_plan_or_generation_binding_mismatch")
    generation = next(
        (o for o in ledger.protocol.operations if o.operation_id == generated.operation_id), None
    )
    if generation is None or (
        generation.action != "source_generate"
        or generation.phase != "development"
        or generation.source_recipe_sha256 != plan.source_recipe_sha256
    ):
        raise SnapshotError("campaign_export_generation_operation_mismatch")
    completion = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == generated.reservation_id
        ),
        None,
    )
    if completion is None or (
        completion.operation_id != generation.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != generated.content_sha256()
    ):
        raise SnapshotError("campaign_export_generation_receipt_not_completed")
    source = next(
        s for s in ledger.protocol.sources if s.content_sha256() == plan.source_recipe_sha256
    )
    p = generated.source.source_parameters
    expected = {
        "profile": source.profile,
        "seed": source.seed,
        "days": (source.history.end - source.history.start).days + 1,
        "products": source.products,
        "stores": source.selling_pairs,
        "warehouses": source.stock_locations,
        "start_date": source.history.start.isoformat(),
        "end_date": source.history.end.isoformat(),
        "business_timezone": "UTC",
    }
    if (
        source.phase != "development"
        or source.generation_config_sha256 != canonical_sha256(p)
        or any(type(p.get(k)) is not type(v) or p.get(k) != v for k, v in expected.items())
        or plan.label_delay_days != source.label_delay_days
    ):
        raise SnapshotError("campaign_export_canonical_source_recipe_mismatch")
    plan.bind(generated.source)
    return source


def _producer(replay: PrivateSourceReplay, source: CampaignSourceRecipe) -> None:
    # Inventory sources pin API/generator dependencies separately from Arrow.
    # New generation plans freeze the exporter lock explicitly before execution.
    exporter_lock = source.exporter_lock_sha256 or replay.declared_exporter_lock_sha256
    _producer_values(replay, source.producer_commit, source.producer_lock_sha256, exporter_lock)


def _producer_values(
    replay: PrivateSourceReplay, commit: str, producer_lock: str, exporter_lock: str | None
) -> None:
    if (
        replay.producer_commit != commit
        or replay.producer_code_state != "clean"
        or replay.producer_lock_sha256 != producer_lock
        or replay.exporter_commit != commit
        or replay.exporter_lock_sha256 is None
        or replay.exporter_lock_sha256 != exporter_lock
    ):
        raise SnapshotError("campaign_export_verified_producer_or_lock_mismatch")


def _store_receipt(
    root: Path,
    receipt: CampaignDevelopmentExportReceipt
    | CampaignGeneratedParentReceipt
    | CampaignFinalExportReceipt
    | CampaignForecastFitReceipt
    | CampaignForecastScoreReceipt
    | CampaignForecastTuneReceipt
    | CampaignForecastCalibrationReceipt
    | CampaignForecastEvaluationReceipt,
) -> None:
    """Keep output evidence durable before completing the charged operation."""
    raw = canonical_bytes(receipt.model_dump(mode="json")) + b"\n"
    if len(raw) > MAX_RECEIPT_BYTES:
        raise SnapshotError("campaign_export_receipt_size_limit")
    directory = root / "receipts"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    checked_directory(directory)
    if stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise SnapshotError("campaign_export_private_receipt_directory_required")
    name, temporary = receipt.reservation_id + ".json", ".export-" + uuid4().hex
    with directory_fd(directory) as fd:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            os.fsync(fd)
        finally:
            os.unlink(temporary, dir_fd=fd)
    with directory_fd(root) as fd:
        os.fsync(fd)


def validate_completed_export(journal: Path, receipt: CampaignDevelopmentExportReceipt) -> None:
    """Validate only the local completion evidence, with no artifact/source read.

    This does not qualify resources, quality, global access or freshness, and
    cannot authorize another source read or fitting outside its own operation.
    """
    receipt = CampaignDevelopmentExportReceipt.model_validate_json(
        canonical_bytes(receipt.model_dump(mode="json"))
    )
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, receipt.operation_id)
    completion = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == receipt.reservation_id
        ),
        None,
    )
    if (
        ledger.protocol_sha256 != receipt.protocol_sha256
        or operation.execution_recipe_sha256 != receipt.plan.content_sha256()
        or operation.source_recipe_sha256 != receipt.plan.source_recipe_sha256
        or ledger.protocol.runtime.code_sha256 != receipt.runtime_code_sha256
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or completion.cost is None
        or completion.cost.artifact_bytes != receipt.artifact_bytes
    ):
        raise SnapshotError("campaign_export_receipt_not_completed")
    try:
        with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                raise SnapshotError("campaign_export_private_receipt_required")
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
    except OSError:
        raise SnapshotError("campaign_export_receipt_unavailable") from None
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_export_stored_receipt_mismatch")


def export_development_forecast(
    snapshot: Path,
    curated: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignDevelopmentExportPlan,
    generated: CampaignGeneratedParentReceipt,
) -> tuple[Path, CampaignDevelopmentExportReceipt]:
    """Reserve the entire parent before metadata, hashes, replay, build or verify.

    Reject wrong operation kinds without consuming an unrelated fit/read slot.
    All binding/source/build/publication failures after reservation are charged.
    A published artifact whose operation failed remains unaccepted evidence.
    """
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignDevelopmentExportPlan.model_validate_json(
            canonical_bytes(plan.model_dump(mode="json"))
        )
        generated = CampaignGeneratedParentReceipt.model_validate_json(
            canonical_bytes(generated.model_dump(mode="json"))
        )
        source = _binding(ledger, operation, plan, generated)
        recipe = plan.bind(generated.source)
        with _open_verified_source_parent(
            snapshot,
            curated,
            generated.source,
            limits=physical_limits(generated.source),
            runtime=ledger.protocol.runtime,
        ) as replay:
            _producer(replay, source)
            destination = _build_physical_forecast(replay, recipe, output_root)
            manifest = verify_physical_forecast(destination)
            if (
                manifest.descriptor.recipe != recipe
                or manifest.descriptor.runtime != ledger.protocol.runtime
                or manifest.descriptor.snapshot_inventory_sha256 != replay.snapshot_inventory_sha256
                or manifest.descriptor.curated_inventory_sha256 != replay.curated_inventory_sha256
                or manifest.descriptor.logical_curated_sha256 != replay.logical_curated_sha256
            ):
                raise SnapshotError("campaign_export_manifest_replay_binding_mismatch")
            receipt = CampaignDevelopmentExportReceipt(
                protocol_sha256=ledger.protocol_sha256,
                operation_id=operation_id,
                reservation_id=str(handle.reservation.reservation_id),
                plan=plan,
                generated_parent_receipt_sha256=generated.content_sha256(),
                recipe=recipe,
                dataset_id=manifest.dataset_id,
                manifest_sha256=file_hash(destination, "manifest.json")[1],
                runtime_code_sha256=ledger.protocol.runtime.code_sha256,
                snapshot_inventory_sha256=replay.snapshot_inventory_sha256,
                curated_inventory_sha256=replay.curated_inventory_sha256,
                logical_curated_sha256=replay.logical_curated_sha256,
                population_rows=manifest.descriptor.feature_descriptor.row_count,
                artifact_bytes=_physical_bytes(destination, recipe.max_artifact_bytes),
            )
            replay.check_parents()
        # The private context's final source/runtime guard must finish before
        # durable receipt publication or a completed journal event can occur.
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, artifact_bytes=receipt.artifact_bytes
        )
    validate_completed_export(journal, receipt)
    return destination, receipt
