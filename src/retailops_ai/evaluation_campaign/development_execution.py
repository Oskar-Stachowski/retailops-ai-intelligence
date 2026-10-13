"""Execute frozen 25/50/100 forecast journals without refunding or retrying attempts.

Controllers own every reservation, native worker, verification and measured cost.
This driver only supplies frozen paths and hands their original receipts onward.
It never generates new parents, changes recipes or authorizes final access.
"""

import logging
import stat
from pathlib import Path
from typing import Annotated, Literal, Self, TypeVar

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignJournal
from retailops_ai.evaluation_campaign.campaign_export import (
    export_development_forecast,
    validate_completed_export,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
)
from retailops_ai.evaluation_campaign.campaign_fit import (
    fit_campaign_forecast,
    verify_campaign_forecast_bundle,
)
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitReceipt
from retailops_ai.evaluation_campaign.campaign_robust_receipt import (
    CanonicalQualificationEvaluationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_score import (
    score_campaign_forecast,
    verify_campaign_forecast_scores,
)
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastScoreReceipt,
)
from retailops_ai.evaluation_campaign.campaign_tune import (
    select_campaign_forecast,
    validate_completed_tune,
)
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.evaluation_campaign.canonical_forecast import verify_canonical_forecast
from retailops_ai.evaluation_campaign.canonical_forecast_contract import (
    CompletedCanonicalForecast,
    PreparedCanonicalForecastProtocol,
)
from retailops_ai.evaluation_campaign.canonical_qualification import (
    PreparedCanonicalQualificationProtocol,
)
from retailops_ai.evaluation_campaign.development_advancement import (
    capture_completed_screening,
    verify_forecast_advancement,
)
from retailops_ai.evaluation_campaign.development_advancement_contract import (
    CompletedForecastAdvancement,
    CompletedForecastScreening,
    MultiscaleSelectionProtocol,
    PreparedForecastAdvancementProtocol,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.preparation_execution import locked, write_once
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    read_bytes,
)

MAX_BYTES = 4 * 1024**2
LOGGER = logging.getLogger(__name__)
Protocol = (
    MultiscaleSelectionProtocol
    | PreparedForecastAdvancementProtocol
    | PreparedCanonicalForecastProtocol
    | PreparedCanonicalQualificationProtocol
)
Receipt = TypeVar("Receipt", bound=Contract)


class DevelopmentParentPaths(Contract):
    snapshot: str
    curated: str


class DevelopmentExecutionPaths(Contract):
    """Frozen before the first export; changing a path cannot create another attempt."""

    version: Literal["ai09-development-execution-paths-1.0.0"] = (
        "ai09-development-execution-paths-1.0.0"
    )
    protocol_sha256: Sha256
    parents: tuple[DevelopmentParentPaths, DevelopmentParentPaths, DevelopmentParentPaths]
    worker_python: str
    output_root: str
    final_test_access_authorized: FalseFlag = False

    @model_validator(mode="after")
    def absolute_paths(self) -> Self:
        paths = [self.worker_python, self.output_root]
        paths.extend(
            value for parent in self.parents for value in (parent.snapshot, parent.curated)
        )
        if any(
            not Path(p).is_absolute() or str(Path(p)) != p or ".." in Path(p).parts for p in paths
        ):
            raise ValueError("development_execution_absolute_normalized_paths_required")
        if len(set(paths)) != len(paths):
            raise ValueError("development_execution_distinct_paths_required")
        return self


def _protocol(ledger: CampaignJournal) -> Protocol:
    protocol = ledger.protocol
    if not isinstance(
        protocol,
        (
            MultiscaleSelectionProtocol,
            PreparedForecastAdvancementProtocol,
            PreparedCanonicalForecastProtocol,
            PreparedCanonicalQualificationProtocol,
        ),
    ):
        raise SnapshotError("development_execution_frozen_multiscale_forecast_required")
    if protocol.runtime != runtime_pin():
        raise SnapshotError("campaign_execution_runtime_changed")
    return protocol


def _completed_prefix(ledger: CampaignJournal) -> dict[str, str]:
    """Any incomplete/failed reservation stops the driver, including a foreign writer."""
    events = ledger.events
    if len(events) % 2:
        raise SnapshotError("development_execution_unfinished_attempt_no_automatic_retry")
    completed: dict[str, str] = {}
    for i in range(0, len(events), 2):
        reserved, finished = events[i : i + 2]
        expected = ledger.protocol.operations[i // 2].operation_id
        if (
            reserved.kind != "reserved"
            or finished.kind != "finished"
            or reserved.operation_id != expected
            or finished.operation_id != expected
            or reserved.reservation_id != finished.reservation_id
            or finished.result != "completed"
        ):
            raise SnapshotError("development_execution_failed_or_unordered_attempt_no_retry")
        completed[expected] = str(finished.reservation_id)
    return completed


def _freeze_paths(journal: Path, paths: DevelopmentExecutionPaths, ledger: CampaignJournal) -> None:
    paths = DevelopmentExecutionPaths.model_validate_json(paths.model_dump_json())
    if paths.protocol_sha256 != ledger.protocol_sha256:
        raise SnapshotError("development_execution_wrong_protocol")
    if any(
        Path(paths.output_root).is_relative_to(Path(parent))
        for parent in (
            str(journal.absolute()),
            *(value for p in paths.parents for value in (p.snapshot, p.curated)),
        )
    ):
        raise SnapshotError("development_execution_output_overlaps_original_evidence")
    name = "forecast-execution-paths.json"
    target = journal / name
    expected = canonical_bytes(paths.model_dump(mode="json")) + b"\n"
    if target.exists() or target.is_symlink():
        if read_bytes(journal, name, MAX_BYTES) != expected:
            raise SnapshotError("development_execution_frozen_paths_changed")
    else:
        if ledger.events:
            raise SnapshotError("development_execution_paths_must_precede_first_operation")
        write_once(target, paths.model_dump(mode="json"))
    output = checked_directory(Path(paths.output_root))
    if stat.S_IMODE(output.stat().st_mode) != 0o700:
        raise SnapshotError("development_execution_private_output_required")


def _receipt(
    journal: Path, completed: dict[str, str], operation: str, kind: type[Receipt]
) -> Receipt:
    return kind.model_validate_json(
        read_bytes(
            journal,
            "receipts/" + completed[operation] + ".json",
            8 * 1024**2 if kind is CanonicalQualificationEvaluationReceipt else MAX_BYTES,
        )
    )


def run_development_forecast(
    journal: Path,
    paths: DevelopmentExecutionPaths,
    *,
    maximum_new_operations: Annotated[int, Field(ge=1, le=16)] = 12,
) -> dict[str, object]:
    """Run a completed prefix onward; failed or interrupted attempts stay consumed.

    An explicit operation limit permits a clean stop between operations. Re-entry
    reopens original metadata; only a new controller reservation can read labels.
    A process dying inside an operation leaves a reservation and cannot auto-resume.
    """
    if type(maximum_new_operations) is not int or not 1 <= maximum_new_operations <= 16:
        raise SnapshotError("development_execution_operation_limit")
    with locked(journal):
        ledger = campaign_journal.inspect(journal)
        protocol = _protocol(ledger)
        if maximum_new_operations > 12 and not isinstance(
            protocol, PreparedCanonicalQualificationProtocol
        ):
            raise SnapshotError("development_execution_operation_limit")
        completed = _completed_prefix(ledger)
        _freeze_paths(journal, paths, ledger)
        from retailops_ai.evaluation_campaign.canonical_qualification_execution import (
            QualificationExecution,
        )

        qualification = (
            QualificationExecution(journal, paths, protocol, ledger)
            if isinstance(protocol, PreparedCanonicalQualificationProtocol)
            else None
        )
        forecast_protocol = (
            protocol.qualification.prefix
            if isinstance(protocol, PreparedCanonicalQualificationProtocol)
            else protocol
        )
        prefix = (
            forecast_protocol.screening
            if isinstance(forecast_protocol, MultiscaleSelectionProtocol)
            else forecast_protocol
        )
        scores = (
            forecast_protocol.screening.scores
            if isinstance(forecast_protocol, MultiscaleSelectionProtocol)
            else (forecast_protocol.score,)
        )
        if isinstance(protocol, PreparedCanonicalQualificationProtocol):
            scores = (*scores, protocol.qualification.calibration_score)
        exports: dict[str, CampaignDevelopmentExportReceipt] = {}
        fits: dict[str, CampaignForecastFitReceipt] = {}
        scored: dict[str, CampaignForecastScoreReceipt] = {}
        selected: CampaignForecastTuneReceipt | None = None
        output, worker = Path(paths.output_root), Path(paths.worker_python)
        new_operations = 0
        for position, operation in enumerate(protocol.operations):
            key = operation.operation_id
            reused = key in completed
            if not reused and new_operations == maximum_new_operations:
                break
            LOGGER.info("AI09 %s %s", "verify_original" if reused else "start", key)
            if qualification is not None and position >= 9:
                if selected is None:
                    raise SnapshotError("canonical_qualification_original_tune_required")
                qualification.step(position, completed, exports, fits, scored, selected)
            elif position < 3:
                if reused:
                    exported = _receipt(journal, completed, key, CampaignDevelopmentExportReceipt)
                    validate_completed_export(journal, exported)
                    if (
                        file_hash(output / exported.dataset_id, "manifest.json")[1]
                        != exported.manifest_sha256
                    ):
                        raise SnapshotError("development_execution_original_export_changed")
                else:
                    parent = paths.parents[position]
                    _, exported = export_development_forecast(
                        Path(parent.snapshot),
                        Path(parent.curated),
                        output,
                        journal=journal,
                        operation_id=key,
                        plan=prefix.exports[position],
                        generated=prefix.parents.generations[position],
                    )
                exports[key] = exported
            elif operation.action == "model_fit":
                plan = prefix.fits[position - 3]
                if reused:
                    fitted = _receipt(journal, completed, key, CampaignForecastFitReceipt)
                    verify_campaign_forecast_bundle(
                        output / fitted.reservation_id / "bundle", journal=journal, receipt=fitted
                    )
                else:
                    exported = exports[plan.export_operation_id]
                    _, fitted = fit_campaign_forecast(
                        output / exported.dataset_id,
                        worker,
                        output,
                        journal=journal,
                        operation_id=key,
                        plan=plan,
                        exported=exported,
                    )
                fits[key] = fitted
            elif position != len(forecast_protocol.operations) - 1:
                score = next(
                    s for s in scores if s.content_sha256() == operation.execution_recipe_sha256
                )
                if reused:
                    result = _receipt(journal, completed, key, CampaignForecastScoreReceipt)
                    verify_campaign_forecast_scores(
                        output / result.reservation_id / "bundle", journal=journal, receipt=result
                    )
                else:
                    exported = exports[score.export_operation_id]
                    family_fits = {f: fits[score.fit_operation_ids[f]] for f in FAMILIES}
                    _, result = score_campaign_forecast(
                        output / exported.dataset_id,
                        {f: output / r.reservation_id / "bundle" for f, r in family_fits.items()},
                        worker,
                        output,
                        journal=journal,
                        operation_id=key,
                        plan=score,
                        exported=exported,
                        fits=family_fits,
                    )
                scored[key] = result
            else:
                tune = forecast_protocol.selection_plan
                if reused:
                    selected = _receipt(journal, completed, key, CampaignForecastTuneReceipt)
                    validate_completed_tune(journal, selected)
                else:
                    exported = exports[tune.export_operation_id]
                    _, selected = select_campaign_forecast(
                        output / exported.dataset_id,
                        {k: output / r.reservation_id / "bundle" for k, r in scored.items()},
                        worker,
                        output,
                        journal=journal,
                        operation_id=key,
                        plan=tune,
                        exported=exported,
                        scores=scored,
                    )
            if not reused:
                new_operations += 1
                # Only controller-published evidence counts, even if a caller returns normally.
                current = campaign_journal.inspect(journal)
                completed = _completed_prefix(current)
                if (
                    current.protocol != protocol
                    or key not in completed
                    or len(completed) != position + 1
                ):
                    raise SnapshotError("development_execution_controller_completion_required")
            LOGGER.info("AI09 completed %s", key)
            if qualification is not None:
                if position == 7 and selected is not None:
                    qualification.verify_tune(selected)
                if qualification.reasons:
                    break
        latest = campaign_journal.inspect(journal)
        completed = _completed_prefix(latest)
        if latest.protocol != protocol:
            raise SnapshotError("development_execution_protocol_changed")
        summary: dict[str, object] = {
            "protocol_sha256": latest.protocol_sha256,
            "journal_head_sha256": latest.head_sha256,
            "completed_operations": len(completed),
            "planned_operations": len(protocol.operations),
            "new_operations": new_operations,
            "status": "incomplete",
            "final_test_access_authorized": False,
            "stage_ready": False,
        }
        if qualification is not None:
            qualification_result = qualification.result(latest)
            if qualification_result is not None:
                document = qualification_result.model_dump(mode="json")
                target = journal / "qualification-execution-result.json"
                if target.exists() or target.is_symlink():
                    if (
                        read_bytes(journal, target.name, MAX_BYTES)
                        != canonical_bytes(document) + b"\n"
                    ):
                        raise SnapshotError("canonical_qualification_original_result_changed")
                else:
                    write_once(target, document)
                summary.update(
                    status=qualification_result.status,
                    development_quality_qualified=qualification_result.development_quality_qualified,
                    quality_qualified=False,
                    reasons=list(qualification_result.reasons),
                )
            return summary
        if selected is not None:
            bundle = output / selected.reservation_id / "bundle"
            evidence: (
                CompletedForecastScreening
                | CompletedForecastAdvancement
                | CompletedCanonicalForecast
            )
            if isinstance(protocol, MultiscaleSelectionProtocol):
                evidence = capture_completed_screening(bundle, journal=journal, selected=selected)
            elif isinstance(protocol, PreparedForecastAdvancementProtocol):
                evidence = verify_forecast_advancement(bundle, journal=journal, selected=selected)
            elif isinstance(protocol, PreparedCanonicalForecastProtocol):
                evidence = verify_canonical_forecast(bundle, journal=journal, selected=selected)
            else:
                raise SnapshotError("development_execution_original_result_protocol_required")
            document = evidence.model_dump(mode="json")
            target = journal / "forecast-execution-result.json"
            if target.exists() or target.is_symlink():
                if read_bytes(journal, target.name, MAX_BYTES) != canonical_bytes(document) + b"\n":
                    raise SnapshotError("development_execution_original_result_changed")
            else:
                write_once(target, document)
            summary["status"] = "completed"
            summary["quality_qualified"] = False
        return summary
