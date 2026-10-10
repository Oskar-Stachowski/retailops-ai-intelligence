"""Execute small full-history preparation through the original audited runner.

This additive journal grants three generation attempts only. It cannot fit,
score, freeze a selection, open final data or close a qualifying campaign.
The original six native preparation phases and stored receipts remain required.
"""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Sha256
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignJournal,
    CampaignOperationPlan,
    CampaignProtocol,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation import generate_campaign_parent
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    CampaignPortfolioProtocol,
    SourceVariant,
)
from retailops_ai.evaluation_campaign.development_profiles import (
    DevelopmentProfilePreparation,
    DevelopmentProfileSource,
)
from retailops_ai.evaluation_campaign.development_search import ForecastDevelopmentSearch
from retailops_ai.source_snapshot.files import SnapshotError


def generation_operations(
    preparation: DevelopmentProfilePreparation,
) -> tuple[CampaignOperationPlan, ...]:
    return tuple(
        CampaignOperationPlan(
            operation_id=f"development-{source.products}-{source.variant}-generate",
            phase="development",
            action="source_generate",
            use_case="source",
            role="all_parent_data",
            source_recipe_sha256=source.content_sha256(),
            execution_recipe_sha256=plan.content_sha256(),
        )
        for source, plan in zip(preparation.sources, preparation.generations, strict=True)
    )


class DevelopmentPreparationProtocol(CampaignProtocol):
    """Reuse the durable budget engine with a distinct preparation-only wire.

    Inherited legacy and policy bindings retain the intended full campaign's
    context, while this subtype explicitly grants no fits or final operations.
    It is rejected by the unchanged canonical protocol contracts.
    """

    development_preparation_version: Literal["ai09-development-preparation-1.0.0"] = (
        "ai09-development-preparation-1.0.0"
    )
    preparation: DevelopmentProfilePreparation
    search: ForecastDevelopmentSearch
    canonical_portfolio_protocol_sha256: Sha256
    sources: Annotated[tuple[DevelopmentProfileSource, ...], Field(min_length=3, max_length=3)]
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=3, max_length=3)]
    maximum_new_attempts: Literal[3] = 3
    maximum_new_fit_attempts: Literal[0] = 0

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        self._frozen_policies()
        expected = (
            self.search.preparation_25_sha256
            if self.preparation.profile.products == 25
            else self.search.preparation_50_sha256
        )
        if (
            self.preparation.content_sha256() != expected
            or self.canonical_portfolio_protocol_sha256
            != self.search.canonical_portfolio_protocol_sha256
            or self.selection_policy_sha256 != self.search.critical_group_policy_sha256
            or self.sources != self.preparation.sources
            or self.operations != generation_operations(self.preparation)
        ):
            raise ValueError("development_preparation_frozen_search_or_generation_mismatch")
        return self


class DevelopmentPreparationJournal(CampaignJournal):
    protocol: DevelopmentPreparationProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        # Reject terminal/selection events before the generic campaign can
        # interpret them. There is no final phase in this separate session.
        if any(event.kind not in {"reserved", "finished"} for event in self.events):
            raise ValueError("development_preparation_only_generation_events_allowed")
        self._replay_events()
        for event in self.events:
            if (
                event.kind == "finished"
                and event.result == "completed"
                and (
                    event.cost is None
                    or event.cost.peak_process_tree_rss_bytes is None
                    or event.cost.artifact_bytes is None
                )
            ):
                raise ValueError("development_preparation_completion_requires_resource_cost")
        return self


def compile_development_preparation(
    journal_path: Path,
    *,
    preparation: DevelopmentProfilePreparation,
    search: ForecastDevelopmentSearch,
    canonical_protocol: CampaignPortfolioProtocol,
) -> DevelopmentPreparationProtocol:
    """Bind an actual declared full campaign and frozen search without data I/O."""
    preparation = DevelopmentProfilePreparation.model_validate_json(preparation.model_dump_json())
    search = ForecastDevelopmentSearch.model_validate_json(search.model_dump_json())
    canonical_protocol = CampaignPortfolioProtocol.model_validate_json(
        canonical_protocol.model_dump_json()
    )
    canonical_source = canonical_protocol.sources[0]
    if (
        search.canonical_portfolio_protocol_sha256 != canonical_protocol.content_sha256()
        or canonical_source.history != preparation.profile.history
        or any(
            getattr(source, name) != getattr(canonical_source, name)
            for source in preparation.sources
            for name in (
                "producer_commit",
                "producer_lock_sha256",
                "exporter_lock_sha256",
                "label_delay_days",
                "selling_pairs",
                "stock_locations",
                "seed",
            )
        )
    ):
        raise ValueError("development_preparation_canonical_producer_or_scope_changed")
    document = canonical_protocol.model_dump(mode="json")
    document.pop("portfolio_version")
    document.pop("training_source_recipe_sha256")
    document.update(
        development_preparation_version="ai09-development-preparation-1.0.0",
        journal_path=str(journal_path.absolute()),
        preparation=preparation.model_dump(mode="json"),
        search=search.model_dump(mode="json"),
        canonical_portfolio_protocol_sha256=canonical_protocol.content_sha256(),
        sources=[source.model_dump(mode="json") for source in preparation.sources],
        operations=[op.model_dump(mode="json") for op in generation_operations(preparation)],
        maximum_new_attempts=3,
        maximum_new_fit_attempts=0,
    )
    return DevelopmentPreparationProtocol.model_validate_json(canonical_bytes(document))


def run_development_preparation_variant(
    *,
    journal: Path,
    variant: SourceVariant,
    producer: Path,
    producer_python: Path,
    output_root: Path,
) -> tuple[Path, Path, CampaignGeneratedParentReceipt]:
    """Run the exact frozen native plan; reserve precedes all producer/output I/O.

    A variant can be invoked once. Completed, failed and interrupted attempts
    remain charged by the original journal. This does not create another journal
    automatically or retry a failure. Independent owned runners may prepare
    different variants; CPU/RAM admission still belongs to each actual runner.
    """
    ledger = campaign_journal.inspect(journal)
    if not isinstance(ledger, DevelopmentPreparationJournal):
        raise SnapshotError("development_preparation_requires_separate_journal")
    position = next(
        (i for i, source in enumerate(ledger.protocol.sources) if source.variant == variant), None
    )
    if position is None:
        raise SnapshotError("development_preparation_unknown_variant")
    return generate_campaign_parent(
        producer,
        producer_python,
        output_root,
        journal=journal,
        operation_id=ledger.protocol.operations[position].operation_id,
        plan=ledger.protocol.preparation.generations[position],
    )
