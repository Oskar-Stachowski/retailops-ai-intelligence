"""Offline ordinary truth from actual Source verification and a public scope census.

The encompassing campaign operation must reserve the read, verify ordinary
generation ancestry and obtain this witness from the fixed producer worker.
A caller-authored witness alone grants no Project qualification or access.
"""

from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.anomaly_detectors.protocol import Scope, Window, scoring_origin, series_key
from retailops_ai.anomaly_evaluation.contract import OrdinaryTruth, TruthWindow
from retailops_ai.data_contracts.common import CommitSha, Contract, FalseFlag, Sha256, SourceID
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation import validate_completed_generation
from retailops_ai.evaluation_campaign.campaign_generation_contract import CampaignGenerationPlan
from retailops_ai.qualified_anomalies.contract import Policy


class OrdinarySourceVerification(Contract):
    version: Literal["ai09-complete-ordinary-source-verification-1.0.0"] = (
        "ai09-complete-ordinary-source-verification-1.0.0"
    )
    source_dataset_id: SourceID
    source_schema_version: Literal["2.7.0"] = "2.7.0"
    source_manifest_sha256: Sha256
    source_descriptor_sha256: Sha256
    source_report_sha256: Sha256
    source_table_inventory_sha256: Sha256
    source_tables: Annotated[int, Field(ge=1, le=256)]
    source_rows: Annotated[int, Field(ge=1, le=20000000)]
    producer_commit: CommitSha
    producer_code_sha256: Sha256
    producer_lock_sha256: Sha256
    exporter_lock_sha256: Sha256
    producer_python_version: str = Field(pattern=r"^3\.11\.[0-9]+$")
    resolved_parameters: dict[str, JsonValue]
    source_scenario_sha256: None = None
    native_reader: Literal["data.inventory.source_dataset_io.read_source_dataset"] = (
        "data.inventory.source_dataset_io.read_source_dataset"
    )
    complete_source_and_reports_replayed: Literal[True] = True
    quality_qualified: FalseFlag = False

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.source_dataset_id != "source-sha256-" + self.source_descriptor_sha256:
            raise ValueError("campaign_ordinary_truth_source_identity")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


def ordinary_source_truth(
    verified: OrdinarySourceVerification,
    scopes: tuple[Scope, ...],
    window: Window,
    *,
    source_dataset_id: str,
    journal: Path,
    reservation_id: str,
    generated: CampaignGeneratedParentReceipt,
    generation_plan: CampaignGenerationPlan,
) -> OrdinaryTruth:
    """Apply native maturity clocks; the caller supplies all verified public scopes."""
    generated = CampaignGeneratedParentReceipt.model_validate_json(generated.model_dump_json())
    generation_plan = CampaignGenerationPlan.model_validate_json(generation_plan.model_dump_json())
    validate_completed_generation(journal, generated)
    ledger = campaign_journal.inspect(journal)
    reservation = next(
        (event for event in ledger.events if str(event.reservation_id) == reservation_id
         and event.kind == "reserved"), None
    )
    operation = next(
        (item for item in ledger.protocol.operations if reservation is not None
         and item.operation_id == reservation.operation_id), None
    )
    generation_operation = next(
        item for item in ledger.protocol.operations if item.operation_id == generated.operation_id
    )
    recipe = next(
        item for item in ledger.protocol.sources
        if item.content_sha256() == generated.source_recipe_sha256
    )
    generation_plan.bind(recipe)
    if (
        operation is None
        or operation.action not in {"source_read", "model_score"}
        or operation.use_case != "anomaly"
        or operation.source_recipe_sha256 != generated.source_recipe_sha256
        or operation.phase != recipe.phase
        or operation.role != ("development_evaluation" if recipe.phase == "development"
                              else "final_evaluation")
        or generated.operation_id not in operation.prerequisites
        or any(event.kind == "finished" and str(event.reservation_id) == reservation_id
               for event in ledger.events)
        or generation_operation.execution_recipe_sha256 != generation_plan.content_sha256()
        or generation_plan.entrypoint != "cached_inventory_v2"
        or generation_plan.scenario_plan is not None
    ):
        raise ValueError("campaign_ordinary_truth_audited_generation_ancestry")
    verified = OrdinarySourceVerification.model_validate_json(verified.model_dump_json())
    window = Window.model_validate_json(window.model_dump_json())
    scopes = tuple(Scope.model_validate_json(scope.model_dump_json()) for scope in scopes)
    keys = [series_key(scope) for scope in scopes]
    parameters = verified.resolved_parameters
    if (
        verified.source_dataset_id != source_dataset_id
        or verified.source_dataset_id != generated.source.parent.source_dataset_id
        or verified.resolved_parameters != generated.source.source_parameters
        or verified.producer_commit != recipe.producer_commit
        or verified.producer_lock_sha256 != recipe.producer_lock_sha256
        or verified.exporter_lock_sha256 != generation_plan.exporter_lock_sha256
        or not keys
        or keys != sorted(set(keys))
        or len(keys) > 10000
        or len(keys) * ((window.end - window.start).days + 1) > 1000000
        or not isinstance(parameters.get("start_date"), str)
        or not isinstance(parameters.get("end_date"), str)
        or not date.fromisoformat(str(parameters["start_date"]))
        <= window.start
        <= window.end
        <= date.fromisoformat(str(parameters["end_date"]))
    ):
        raise ValueError("campaign_ordinary_truth_scope_source_or_window")
    return OrdinaryTruth(
        source_dataset_id=verified.source_dataset_id,
        source_verification_sha256=verified.content_sha256(),
        source_generation_receipt_sha256=generated.content_sha256(),
        complete_windows=tuple(
            TruthWindow(
                **scope.model_dump(),
                window=window,
                available_at=scoring_origin(window.end, scope.event_type, Policy()),
            )
            for scope in scopes
        ),
    )
