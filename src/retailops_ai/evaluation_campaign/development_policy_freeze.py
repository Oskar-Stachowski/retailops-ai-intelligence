"""Bind actual policy documents to preparation, without invented policy digests."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.anomaly_evaluation.contract import EvaluationPolicy
from retailops_ai.anomaly_evaluation.quality import QualityPolicy
from retailops_ai.data_contracts.common import CommitSha, Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastQualityPolicy,
)
from retailops_ai.evaluation_campaign.campaign_generation import _producer_pin
from retailops_ai.evaluation_campaign.campaign_required_group_recipe import (
    CampaignRequiredGroupRecipe,
)
from retailops_ai.evaluation_campaign.campaign_uncertainty_contract import (
    CampaignForecastUncertaintyPolicy,
)
from retailops_ai.evaluation_campaign.contract import SCENARIOS, PreparationRuntime
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
)
from retailops_ai.evaluation_campaign.legacy_carryover import (
    LegacyCampaignCarryover,
    load_legacy_carryover,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.preparation_execution import write_once
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, read_bytes
from retailops_ai.stockout.contract import LabelPolicy
from retailops_ai.stockout_policy.contract import PolicySpec
from retailops_ai.stockout_selection.contract import QualityRequirements


class SourceCatalogPolicyPin(Contract):
    producer_commit: CommitSha
    code_sha256: dict[str, Sha256]
    category_inventory: Annotated[tuple[Symbol, ...], Field(min_length=1, max_length=32)]
    method: Literal[
        "native_catalog_metadata_from_pinned_source_code_before_any_dataset_generation"
    ] = "native_catalog_metadata_from_pinned_source_code_before_any_dataset_generation"

    @model_validator(mode="after")
    def complete(self) -> Self:
        if set(self.code_sha256) != {
            "data/generator/dimensions.py",
            "data/generator/dimension_schema.py",
            "data/generator/common.py",
            "data/generator/business_calendar.py",
        } or self.category_inventory != tuple(sorted(set(self.category_inventory))):
            raise ValueError("development_policy_complete_source_catalog_pin_required")
        return self


class DevelopmentPolicySet(Contract):
    """One prospective rule set, shared by every development scale and final seed.

    The anomaly reference is the existing AI07 numerical policy, not a claim
    that new data belong to its original campaign. Current evaluation still
    requires a separately bound AI09 report and original source evidence.
    """

    version: Literal["ai09-prospective-development-policies-1.0.0"] = (
        "ai09-prospective-development-policies-1.0.0"
    )
    catalog: SourceCatalogPolicyPin
    required_groups: CampaignRequiredGroupRecipe
    forecast_quality: CampaignForecastQualityPolicy = CampaignForecastQualityPolicy()
    anomaly_reference_quality: QualityPolicy
    anomaly_metrics: EvaluationPolicy = EvaluationPolicy()
    stockout_quality: dict[Literal["development", "final"], QualityRequirements]
    stockout_labels: LabelPolicy = LabelPolicy()
    stockout_decision_reference: PolicySpec
    stockout_metric_rules: Literal[
        "native_AP_above_no_skill_Brier_below_train_constant_ECE_and_complete_segment_support"
    ] = "native_AP_above_no_skill_Brier_below_train_constant_ECE_and_complete_segment_support"
    uncertainty: CampaignForecastUncertaintyPolicy = CampaignForecastUncertaintyPolicy()
    seed_weights: dict[str, Annotated[int, Field(ge=1, le=1000)]]
    scenario_weights: dict[str, Annotated[int, Field(ge=1, le=1000)]]
    actual_source_catalog_verified: FalseFlag = False
    model_fit_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        if (
            self.catalog.category_inventory
            != self.required_groups.segment_policy.category_inventory
            or set(self.seed_weights) != {"42", "137", "2026"}
            or set(self.scenario_weights) != set(SCENARIOS)
            or set(self.stockout_quality) != {"development", "final"}
        ):
            raise ValueError("development_policy_complete_inventory_required")
        for phase, locations in (("development", 3), ("final", 4)):
            policy = self.stockout_quality[phase]  # type: ignore[index]
            if (policy.expected_category_count, policy.expected_stock_location_count) != (
                len(self.catalog.category_inventory),
                locations,
            ):
                raise ValueError("development_policy_stockout_complete_dimensions_required")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))

    def context(
        self, legacy: LegacyCampaignCarryover, runtime: PreparationRuntime
    ) -> DevelopmentPlanningContext:
        """Derive every policy digest from these actual, revalidated documents."""
        self = type(self).model_validate_json(self.model_dump_json())
        return DevelopmentPlanningContext(
            legacy=legacy,
            legacy_sha256=legacy.content_sha256(),
            runtime=runtime,
            selection_policy_sha256=self.required_groups.content_sha256(),
            use_case_quality_policy_sha256={
                "forecast": self.forecast_quality.content_sha256(),
                "anomaly": canonical_sha256(
                    {
                        "quality": self.anomaly_reference_quality.model_dump(mode="json"),
                        "metrics": self.anomaly_metrics.model_dump(mode="json"),
                    }
                ),
                "stockout": canonical_sha256(
                    {
                        "requirements": {
                            k: v.model_dump(mode="json") for k, v in self.stockout_quality.items()
                        },
                        "metric_rules": self.stockout_metric_rules,
                        "labels": self.stockout_labels.model_dump(mode="json"),
                        "decision_reference": self.stockout_decision_reference.model_dump(
                            mode="json"
                        ),
                    }
                ),
            },
            segment_policy_sha256=self.required_groups.segment_policy.content_sha256(),
            uncertainty_policy_sha256=self.uncertainty.content_sha256(),
            seed_weights=self.seed_weights,
            scenario_weights=self.scenario_weights,
        )


class FrozenDevelopmentPolicies(Contract):
    version: Literal["ai09-frozen-development-policy-context-1.0.0"] = (
        "ai09-frozen-development-policy-context-1.0.0"
    )
    policies: DevelopmentPolicySet
    policies_sha256: Sha256
    context: DevelopmentPlanningContext
    final_test_access_authorized: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def original_documents(self) -> Self:
        if (
            self.policies.content_sha256() != self.policies_sha256
            or self.context != self.policies.context(self.context.legacy, self.context.runtime)
        ):
            raise ValueError("development_policy_context_requires_actual_original_documents")
        return self

    def require_producer(self, commit: str) -> None:
        if self.policies.catalog.producer_commit != commit:
            raise SnapshotError("development_policy_source_catalog_producer_changed")


def verify_catalog_policy_pin(pin: SourceCatalogPolicyPin, producer: Path) -> None:
    """Read native catalog metadata from the exact clean code, without generating data."""
    pin = SourceCatalogPolicyPin.model_validate_json(pin.model_dump_json())
    _producer_pin(producer, pin.producer_commit)
    for name, expected in pin.code_sha256.items():
        if file_hash(producer, name)[1] != expected:
            raise SnapshotError("development_policy_source_catalog_code_changed")
    raw = subprocess.check_output(  # noqa: S603 -- exact pinned native code, no shell
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys,json; sys.path.insert(0,sys.argv[1]); "
            "from data.generator.dimensions import _categories; "
            "print(json.dumps(sorted(row['id'] for row in _categories())))",
            str(producer.resolve()),
        ],
        cwd=producer,
        env={k: os.environ[k] for k in ("PATH", "LANG", "SYSTEMROOT") if k in os.environ},
        timeout=30,
    )
    _producer_pin(producer, pin.producer_commit)
    if len(raw) > 16384 or json.loads(raw) != list(pin.category_inventory):
        raise SnapshotError("development_policy_source_catalog_inventory_changed")


def freeze_development_policies(
    output: Path, *, policies: DevelopmentPolicySet, evidence: Path, producer: Path
) -> FrozenDevelopmentPolicies:
    """Write once before initialization; loading legacy metadata never refunds its slots."""
    policies = DevelopmentPolicySet.model_validate_json(policies.model_dump_json())
    verify_catalog_policy_pin(policies.catalog, producer)
    result = FrozenDevelopmentPolicies(
        policies=policies,
        policies_sha256=policies.content_sha256(),
        context=policies.context(load_legacy_carryover(evidence), runtime_pin()),
    )
    write_once(output, result.model_dump(mode="json"))
    return result


def load_frozen_development_policies(path: Path) -> FrozenDevelopmentPolicies:
    result = FrozenDevelopmentPolicies.model_validate_json(
        read_bytes(path.parent, path.name, 4 * 1024**2)
    )
    if result.context.runtime != runtime_pin():
        raise SnapshotError("development_policy_frozen_runtime_changed")
    return result
