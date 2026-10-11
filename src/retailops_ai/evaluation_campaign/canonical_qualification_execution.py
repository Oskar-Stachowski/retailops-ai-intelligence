"""Calibration and all three independent evaluations through original controllers."""

import stat
from pathlib import Path
from typing import TYPE_CHECKING

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_calibration import (
    fit_campaign_forecast_calibration,
    verify_campaign_forecast_calibration,
)
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibrationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_context_bundle import (
    build_campaign_context_bundle,
    verify_campaign_context_bundle,
)
from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
    CampaignContextBundleReceipt,
)
from retailops_ai.evaluation_campaign.campaign_contract import CampaignJournal
from retailops_ai.evaluation_campaign.campaign_evaluation import (
    evaluate_campaign_forecast,
    verify_campaign_forecast_evaluation,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_configuration import (
    bind_forecast_configuration,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
)
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitReceipt
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import VARIANTS
from retailops_ai.evaluation_campaign.campaign_robust_receipt import (
    CanonicalQualificationEvaluationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScoreReceipt
from retailops_ai.evaluation_campaign.campaign_tune import verify_campaign_forecast_selection
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.evaluation_campaign.canonical_qualification import (
    PreparedCanonicalQualificationProtocol,
)
from retailops_ai.evaluation_campaign.canonical_qualification_execution_contract import (
    CanonicalQualificationExecutionResult,
)
from retailops_ai.evaluation_campaign.development_advancement import (
    _completed_costs,
    verified_family_metrics,
)
from retailops_ai.evaluation_campaign.preparation_execution import write_once
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.source_snapshot.files import SnapshotError, checked_directory, read_bytes

if TYPE_CHECKING:
    from retailops_ai.evaluation_campaign.development_execution import DevelopmentExecutionPaths


class QualificationExecution:
    """A fresh in-memory view on every invocation; original evidence owns all state."""

    def __init__(
        self,
        journal: Path,
        paths: "DevelopmentExecutionPaths",
        protocol: PreparedCanonicalQualificationProtocol,
        ledger: CampaignJournal,
    ) -> None:
        self.journal, self.paths, self.protocol = journal, paths, protocol
        self.output = Path(paths.output_root)
        self.qualified_output = self.output.with_name(self.output.name + "-qualification")
        self.calibration_output = self.qualified_output / "calibration"
        self.context_output = self.qualified_output / "contexts"
        self.evaluation_output = self.qualified_output / "evaluations"
        self.calibration: CampaignForecastCalibrationReceipt | None = None
        self.contexts: dict[int, CampaignContextBundleReceipt] = {}
        self.evaluations: dict[int, CanonicalQualificationEvaluationReceipt] = {}
        self.reasons: tuple[str, ...] = ()
        self._freeze_output(ledger)

    def _freeze_output(self, ledger: CampaignJournal) -> None:
        root = self.qualified_output
        for parent in (
            self.journal,
            self.output,
            *(Path(p) for item in self.paths.parents for p in (item.snapshot, item.curated)),
        ):
            a, b = root.resolve(), parent.resolve()
            if a.is_relative_to(b) or b.is_relative_to(a):
                raise SnapshotError("canonical_qualification_distinct_output_required")
        target = self.journal / "qualification-execution-paths.json"
        document = {
            "version": "ai09-canonical-qualification-execution-paths-1.0.0",
            "protocol_sha256": ledger.protocol_sha256,
            "output_root": str(root),
            "calibration_output_root": str(self.calibration_output),
            "context_output_root": str(self.context_output),
            "evaluation_output_root": str(self.evaluation_output),
            "final_test_access_authorized": False,
        }
        if target.exists() or target.is_symlink():
            if read_bytes(self.journal, target.name) != canonical_bytes(document) + b"\n":
                raise SnapshotError("canonical_qualification_frozen_output_changed")
        else:
            if ledger.events:
                raise SnapshotError("canonical_qualification_paths_must_precede_first_operation")
            write_once(target, document)
        for directory in (
            root,
            self.calibration_output,
            self.context_output,
            self.evaluation_output,
        ):
            if not directory.exists() and not directory.is_symlink():
                if ledger.events:
                    raise SnapshotError("canonical_qualification_original_output_missing")
                directory.mkdir(mode=0o700)
            checked_directory(directory)
            if stat.S_IMODE(directory.stat().st_mode) != 0o700:
                raise SnapshotError("canonical_qualification_private_output_required")

    def verify_tune(self, selected: CampaignForecastTuneReceipt) -> None:
        bundle = self.output / selected.reservation_id / "bundle"
        verify_campaign_forecast_selection(bundle, journal=self.journal, receipt=selected)
        _, self.reasons = verified_family_metrics(bundle, selected)

    def step(
        self,
        position: int,
        completed: dict[str, str],
        exports: dict[str, CampaignDevelopmentExportReceipt],
        fits: dict[str, CampaignForecastFitReceipt],
        scores: dict[str, CampaignForecastScoreReceipt],
        selected: CampaignForecastTuneReceipt,
    ) -> None:
        from retailops_ai.evaluation_campaign.development_execution import _receipt

        plan = self.protocol.qualification
        key = self.protocol.operations[position].operation_id
        reused = key in completed
        worker = Path(self.paths.worker_python)
        tune_bundle = self.output / selected.reservation_id / "bundle"
        if position == 9:
            if reused:
                self.calibration = _receipt(
                    self.journal, completed, key, CampaignForecastCalibrationReceipt
                )
                verify_campaign_forecast_calibration(
                    self.calibration_output / self.calibration.reservation_id / "bundle",
                    journal=self.journal,
                    receipt=self.calibration,
                )
            else:
                exported = exports[plan.calibration.export_operation_id]
                cal_scores = {k: scores[k] for k in plan.calibration.score_operation_ids}
                _, self.calibration = fit_campaign_forecast_calibration(
                    self.output / exported.dataset_id,
                    tune_bundle,
                    {k: self.output / s.reservation_id / "bundle" for k, s in cal_scores.items()},
                    worker,
                    self.calibration_output,
                    journal=self.journal,
                    operation_id=key,
                    plan=plan.calibration,
                    exported=exported,
                    tune=selected,
                    scores=cal_scores,
                )
            if self.calibration.calibration.status == "not_ready":
                self.reasons = ("calibration_not_ready",)
            return
        if not 10 <= position < 16 or self.calibration is None or self.reasons:
            raise SnapshotError("canonical_qualification_verified_calibration_required")
        variant = (position - 10) // 2
        exported = exports[plan.contexts[variant].export_operation_id]
        if position % 2 == 0:
            if reused:
                context = _receipt(self.journal, completed, key, CampaignContextBundleReceipt)
                verify_campaign_context_bundle(
                    self.context_output / context.reservation_id / "bundle",
                    journal=self.journal,
                    receipt=context,
                )
            else:
                parents = plan.prefix.parents
                generation = (
                    parents.preparation.bootstrap.generation
                    if variant == 0
                    else parents.preparation.planned.preparation.generations[variant]
                )
                paths = self.paths.parents[variant]
                _, context = build_campaign_context_bundle(
                    Path(paths.snapshot),
                    Path(paths.curated),
                    self.output / exported.dataset_id,
                    self.context_output,
                    journal=self.journal,
                    operation_id=key,
                    recipe=plan.contexts[variant],
                    generation=generation,
                    generated=parents.generations[variant],
                    exported=exported,
                )
            self.contexts[variant] = context
            return
        if reused:
            evaluated = _receipt(
                self.journal, completed, key, CanonicalQualificationEvaluationReceipt
            )
            verify_campaign_forecast_evaluation(
                self.evaluation_output / evaluated.reservation_id / "bundle",
                journal=self.journal,
                receipt=evaluated,
            )
        else:
            configuration = bind_forecast_configuration(
                selected,
                self.calibration,
                tuple(scores[k] for k in plan.prefix.selection_plan.score_operation_ids),
                tuple(scores[k] for k in plan.calibration.score_operation_ids),
                fits,
                feature_schema_sha256=canonical_sha256(InputRow.model_json_schema()),
                quality_policy=plan.policies.policies.forecast_quality,
            )
            context = self.contexts[variant]
            _, receipt = evaluate_campaign_forecast(
                self.output / exported.dataset_id,
                {k: self.output / fit.reservation_id / "bundle" for k, fit in fits.items()},
                tune_bundle,
                self.calibration_output / self.calibration.reservation_id / "bundle",
                worker,
                self.evaluation_output,
                journal=self.journal,
                operation_id=key,
                recipe=plan.evaluations[variant],
                exported=exported,
                configuration=configuration,
                tune=selected,
                calibration=self.calibration,
                fits=fits,
                raw_context_bundle=self.context_output / context.reservation_id / "bundle",
                raw_context_receipt=context,
                uncertainty_policy=plan.policies.policies.uncertainty,
                required_group_policy=plan.policies.policies.required_groups,
            )
            if not isinstance(receipt, CanonicalQualificationEvaluationReceipt):
                raise SnapshotError("canonical_qualification_complete_evaluation_receipt_required")
            evaluated = receipt
        self.evaluations[variant] = evaluated

    def result(self, ledger: CampaignJournal) -> CanonicalQualificationExecutionResult | None:
        events = [e for e in ledger.events if e.kind == "finished"]
        if not self.reasons and len(events) < 16:
            return None
        receipts: dict[str, str] = {}
        for event in events:
            if event.operation_id is None or event.evidence_sha256 is None:
                raise SnapshotError("canonical_qualification_original_receipt_hash_required")
            receipts[event.operation_id] = event.evidence_sha256
        return CanonicalQualificationExecutionResult(
            protocol=self.protocol,
            journal_head_sha256=ledger.head_sha256,
            operation_receipt_sha256=receipts,
            operation_costs=_completed_costs(ledger, len(events)),
            calibration_status=(
                self.calibration.calibration.status if self.calibration else "not_attempted"
            ),
            evaluation_quality={
                VARIANTS[i]: receipt.quality_qualified for i, receipt in self.evaluations.items()
            },
            reasons=self.reasons,
            status="not_ready" if self.reasons else "completed",
            development_quality_qualified=(
                len(self.evaluations) == 3
                and all(receipt.quality_qualified for receipt in self.evaluations.values())
            ),
        )
