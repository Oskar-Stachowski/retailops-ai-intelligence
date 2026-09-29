"""Formal feature, label and development split contracts for AI 04.3."""

from datetime import date, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, StrictBool, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    DateWindow,
    FeatureID,
    ForecastKey,
    LabelID,
    Sha256,
    SplitID,
    Symbol,
    UtcTime,
    end_of_day,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.contract import Parent
from retailops_ai.forecasting.features_contract import FEATURE_TYPES


class PreprocessingRecipe(Contract):
    version: Literal["forecast-train-only-1.0.0"] = "forecast-train-only-1.0.0"
    fit_scope: Literal["eligible_train_rows_of_one_fold_only"] = (
        "eligible_train_rows_of_one_fold_only"
    )
    numeric_missing: Literal["train_median_with_indicator"] = "train_median_with_indicator"
    boolean_missing: Literal["train_mode_tie_false_with_indicator"] = (
        "train_mode_tie_false_with_indicator"
    )
    entirely_missing: Literal["constant_zero_with_indicator"] = "constant_zero_with_indicator"
    categorical: Literal["train_vocabulary_one_hot_with_missing_and_unknown"] = (
        "train_vocabulary_one_hot_with_missing_and_unknown"
    )
    scaling: Literal["none"] = "none"
    output_numbers: Literal["float64_model_inputs_raw_money_remains_exact"] = (
        "float64_model_inputs_raw_money_remains_exact"
    )


class FeaturePolicy(Contract):
    version: Literal["forecast-features-1.0.0"] = "forecast-features-1.0.0"
    columns: tuple[str, ...] = tuple(FEATURE_TYPES)
    minimum_active_history_days: Annotated[int, Field(ge=1, le=28)] = 28
    minimum_known_history_days: Annotated[int, Field(ge=1, le=28)] = 7
    maximum_observation_age_days: Annotated[int, Field(ge=1, le=28)] = 3
    missing_features: Literal["retain_nullable_values_and_fit_train_only"] = (
        "retain_nullable_values_and_fit_train_only"
    )
    cold_start: Literal["insufficient_data_no_prediction"] = "insufficient_data_no_prediction"
    stale_history: Literal["block_run_no_partial_success"] = "block_run_no_partial_success"
    closed_target: Literal["retain_in_coverage_exclude_from_scoring"] = (
        "retain_in_coverage_exclude_from_scoring"
    )
    inventory: Literal["excluded"] = "excluded"
    truth: Literal["excluded"] = "excluded"
    preprocessing: PreprocessingRecipe = PreprocessingRecipe()

    @model_validator(mode="after")
    def allowlist(self) -> Self:
        if (
            not self.columns
            or len(set(self.columns)) != len(self.columns)
            or any(c not in FEATURE_TYPES for c in self.columns)
        ):
            raise ValueError("formal_feature_allowlist_invalid")
        if self.minimum_known_history_days > self.minimum_active_history_days:
            raise ValueError("formal_feature_minimum_history_invalid")
        return self


class CodePin(Contract):
    version: Literal["forecast-manifests-1.0.0"] = "forecast-manifests-1.0.0"
    code_files: dict[str, Sha256]
    code_sha256: Sha256
    dependency_lock_sha256: Sha256
    python_version: str
    pyarrow_version: str

    @model_validator(mode="after")
    def hashes(self) -> Self:
        if not self.code_files or self.code_sha256 != canonical_sha256(self.code_files):
            raise ValueError("forecast_manifest_code_pin_invalid")
        return self


class FeatureDescriptor(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    role: Literal["features"] = "features"
    canonicalization: Literal["retailops-canonical-json-v1"] = "retailops-canonical-json-v1"
    parent: Parent
    calendar_id: Annotated[str, Field(pattern=r"^forecast-calendar-sha256-[0-9a-f]{64}$")]
    inputs_id: Annotated[str, Field(pattern=r"^forecast-inputs-sha256-[0-9a-f]{64}$")]
    source_parameters: dict[str, JsonValue]
    requested_policy: FeaturePolicy
    resolved_policy: FeaturePolicy
    feature_types: dict[str, str]
    code: CodePin
    input_code_sha256: Sha256
    content_sha256: Sha256
    history_content_sha256: Sha256
    row_count: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def resolved(self) -> Self:
        if self.requested_policy != self.resolved_policy or self.feature_types != {
            c: FEATURE_TYPES[c] for c in self.resolved_policy.columns
        }:
            raise ValueError("formal_feature_policy_or_types_mismatch")
        return self


class FeatureManifest(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    feature_set_id: FeatureID
    descriptor: FeatureDescriptor
    generated_at: UtcTime
    forecast_model_status: Literal["not_ready"] = "not_ready"

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.feature_set_id != "features-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ):
            raise ValueError("formal_feature_identity_mismatch")
        return self


Role = Literal["train", "validation", "development_holdout", "purged"]


class FoldPlan(Contract):
    name: Symbol
    train: DateWindow
    validation: DateWindow
    development_holdout: DateWindow
    purge_days: Annotated[int, Field(ge=14, le=90)] = 15
    training_cutoff: UtcTime
    selection_cutoff: UtcTime
    evaluation_cutoff: UtcTime

    @model_validator(mode="after")
    def chronology(self) -> Self:
        if any(
            b.start <= a.end + timedelta(days=self.purge_days)
            for a, b in zip(
                (self.train, self.validation),
                (self.validation, self.development_holdout),
                strict=True,
            )
        ):
            raise ValueError("forecast_split_overlap_or_short_purge")
        if (
            not end_of_day(self.train.end)
            <= self.training_cutoff
            < end_of_day(self.validation.start)
        ):
            raise ValueError("forecast_training_cutoff_after_validation_origin")
        if (
            not end_of_day(self.validation.end)
            <= self.selection_cutoff
            < end_of_day(self.development_holdout.start)
        ):
            raise ValueError("forecast_selection_cutoff_after_holdout_origin")
        if self.evaluation_cutoff < end_of_day(self.development_holdout.end):
            raise ValueError("forecast_evaluation_cutoff_before_holdout_origin")
        return self

    def role(self, day: date) -> Role:
        for role in ("train", "validation", "development_holdout"):
            window = getattr(self, role)
            if window.start <= day <= window.end:
                return role
        return "purged"

    def label_cutoff(self, role: Role) -> UtcTime:
        return (
            self.training_cutoff
            if role == "train"
            else self.selection_cutoff
            if role == "validation"
            else self.evaluation_cutoff
        )


class SplitPolicy(Contract):
    version: Literal["forecast-development-split-1.0.0"] = "forecast-development-split-1.0.0"
    protocol: Literal["fixed_origin_development_only"] = "fixed_origin_development_only"
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"
    folds: tuple[FoldPlan, ...] = Field(min_length=1, max_length=10)
    label_policy: Literal["latest_complete_known_version_at_role_cutoff"] = (
        "latest_complete_known_version_at_role_cutoff"
    )
    missing_label: Literal["censored_never_zero"] = "censored_never_zero"
    minimum_eligible_rows_per_role: Annotated[int, Field(ge=1, le=100000)] = 1

    @model_validator(mode="after")
    def unique_folds(self) -> Self:
        if len({f.name for f in self.folds}) != len(self.folds):
            raise ValueError("duplicate_forecast_fold")
        return self


class LabelPoint(ForecastKey):
    fold: Symbol
    role: Literal["train", "validation", "development_holdout"]
    knowledge_cutoff: UtcTime
    status: Literal["eligible", "censored"]
    observed_sales_units: Annotated[int, Field(ge=0)] | None
    label_available_at: UtcTime | None
    source_record_sha256: Sha256 | None
    source_record_id: str | None
    version: Annotated[int, Field(ge=1)] | None
    reason: Literal["missing_or_unavailable", "incomplete_source"] | None

    @model_validator(mode="after")
    def maturity(self) -> Self:
        if self.status == "eligible":
            if (
                self.observed_sales_units is None
                or self.label_available_at is None
                or self.source_record_sha256 is None
                or self.source_record_id is None
                or self.version is None
                or self.reason is not None
            ):
                raise ValueError("eligible_forecast_label_incomplete")
            if not end_of_day(self.target_date) <= self.label_available_at <= self.knowledge_cutoff:
                raise ValueError("forecast_label_not_mature_at_cutoff")
        elif (
            any(
                v is not None
                for v in (
                    self.observed_sales_units,
                    self.label_available_at,
                    self.source_record_sha256,
                    self.source_record_id,
                    self.version,
                )
            )
            or self.reason is None
        ):
            raise ValueError("censored_forecast_label_is_not_zero")
        return self


Reason = Literal[
    "purged_origin",
    "insufficient_history",
    "stale_history",
    "unknown_calendar",
    "closed_target",
    "censored_label",
]


class Membership(ForecastKey):
    fold: Symbol
    role: Role
    eligible: StrictBool
    reasons: tuple[Reason, ...]
    label_content_sha256: Sha256 | None

    @model_validator(mode="after")
    def qualification(self) -> Self:
        if self.eligible != (not self.reasons) or len(set(self.reasons)) != len(self.reasons):
            raise ValueError("forecast_membership_eligibility_mismatch")
        if (self.role == "purged") != (self.label_content_sha256 is None):
            raise ValueError("forecast_membership_label_reference_mismatch")
        if self.role == "purged" and self.reasons != ("purged_origin",):
            raise ValueError("forecast_purged_membership_invalid")
        return self


class FileReceipt(Contract):
    path: str
    size_bytes: Annotated[int, Field(ge=1, le=2 * 1024**3)]
    sha256: Sha256
    row_count: Annotated[int, Field(ge=1, le=10000000)]


class TableReceipt(Contract):
    content_sha256: Sha256
    row_count: Annotated[int, Field(ge=0, le=10000000)]
    files: tuple[FileReceipt, ...] = Field(max_length=10000)


class LabelDescriptor(Contract):
    role: Literal["labels"] = "labels"
    parent: Parent
    feature_set_id: FeatureID
    policy: SplitPolicy
    code: CodePin
    content_sha256: Sha256
    row_count: Annotated[int, Field(ge=0)]


class SplitDescriptor(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    role: Literal["split"] = "split"
    feature_set_id: FeatureID
    label_dataset_id: LabelID
    parent: Parent
    requested_policy: SplitPolicy
    resolved_policy: SplitPolicy
    feature_policy: FeaturePolicy
    code: CodePin
    content_sha256: Sha256
    row_count: Annotated[int, Field(ge=1)]
    counts: dict[str, Annotated[int, Field(ge=0)]]
    qualification_status: Literal["passed", "not_ready"]

    @model_validator(mode="after")
    def resolved(self) -> Self:
        if self.requested_policy != self.resolved_policy:
            raise ValueError("forecast_split_unresolved_policy")
        ready = not self.counts.get("stale_history", 0) and all(
            self.counts.get(f.name + ":" + role + ":eligible", 0)
            >= self.resolved_policy.minimum_eligible_rows_per_role
            for f in self.resolved_policy.folds
            for role in ("train", "validation", "development_holdout")
        )
        if self.qualification_status != ("passed" if ready else "not_ready"):
            raise ValueError("forecast_split_qualification_status_mismatch")
        return self


class SplitManifest(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    split_id: SplitID
    descriptor: SplitDescriptor
    labels: LabelDescriptor
    tables: dict[str, TableReceipt]
    generated_at: UtcTime
    forecast_model_status: Literal["not_ready"] = "not_ready"

    @model_validator(mode="after")
    def identities(self) -> Self:
        if self.split_id != "split-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ) or self.descriptor.label_dataset_id != "labels-sha256-" + canonical_sha256(
            self.labels.model_dump(mode="json")
        ):
            raise ValueError("forecast_split_or_label_identity_mismatch")
        if (
            self.labels.feature_set_id != self.descriptor.feature_set_id
            or self.labels.parent != self.descriptor.parent
            or self.labels.policy != self.descriptor.resolved_policy
            or self.labels.code != self.descriptor.code
        ):
            raise ValueError("forecast_split_label_parents_or_policy_mismatch")
        if (
            set(self.tables) != {"labels", "memberships"}
            or self.tables["labels"].content_sha256 != self.labels.content_sha256
            or self.tables["labels"].row_count != self.labels.row_count
            or self.tables["memberships"].content_sha256 != self.descriptor.content_sha256
            or self.tables["memberships"].row_count != self.descriptor.row_count
        ):
            raise ValueError("forecast_split_table_content_mismatch")
        return self
