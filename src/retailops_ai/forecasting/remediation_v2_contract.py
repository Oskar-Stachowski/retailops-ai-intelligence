"""Versioned validation-only repair recipe; original quality thresholds remain frozen."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FeatureID, Sha256, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.manifest_contract import FileReceipt
from retailops_ai.forecasting.quality_contract import QualityPolicy, QualityStatus


class RemediationPolicy(Contract):
    version: Literal["forecast-quality-remediation-2.0.0", "forecast-quality-remediation-2.1.0"] = (
        "forecast-quality-remediation-2.1.0"
    )
    quality: QualityPolicy = QualityPolicy()
    groups: Literal["origin_known_volume_and_category"] = "origin_known_volume_and_category"
    correction: Literal["first_half_mean_ratio_candidates_with_frozen_baseline_blends"] = (
        "first_half_mean_ratio_candidates_with_frozen_baseline_blends"
    )
    selection: Literal["two_block_bias_and_mae_guarded_selection_with_baseline_fallback"] = (
        "two_block_bias_and_mae_guarded_selection_with_baseline_fallback"
    )
    calibration: Literal[
        "second_half_scaled_shortest_nominal_residual_window",
        "second_half_validation_signed_residual_equal_tail_quantiles",
    ] = "second_half_validation_signed_residual_equal_tail_quantiles"
    calibration_fallback: tuple[
        Literal["volume_category"], Literal["volume"], Literal["global"]
    ] = ("volume_category", "volume", "global")
    minimum_fit_rows: Annotated[int, Field(ge=30, le=100000)] = 30
    minimum_selection_rows: Annotated[int, Field(ge=30, le=100000)] = 30
    minimum_correction_ratio: Annotated[float, Field(ge=0.5, le=0.5)] = 0.5
    maximum_correction_ratio: Annotated[float, Field(ge=2, le=2)] = 2.0
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"

    @model_validator(mode="after")
    def frozen_thresholds(self) -> Self:
        if self.quality != QualityPolicy():
            raise ValueError("remediation_must_preserve_original_quality_thresholds")
        if (self.version == "forecast-quality-remediation-2.0.0") != (
            self.calibration == "second_half_scaled_shortest_nominal_residual_window"
        ):
            raise ValueError("remediation_version_calibration_mismatch")
        return self


class RemediationDescriptor(Contract):
    kind: Literal["forecast_quality_remediation"] = "forecast_quality_remediation"
    backtest_id: Annotated[str, Field(pattern=r"^forecast-backtest-sha256-[0-9a-f]{64}$")]
    feature_set_id: FeatureID
    policy: RemediationPolicy
    code_files: dict[str, Sha256]
    code_sha256: Sha256
    quality_status: QualityStatus
    gate_counts: dict[QualityStatus, Annotated[int, Field(ge=0)]]
    report_sha256: dict[str, Sha256]

    @model_validator(mode="after")
    def identity_and_status(self) -> Self:
        if not self.code_files or canonical_sha256(self.code_files) != self.code_sha256:
            raise ValueError("remediation_code_hash_mismatch")
        if set(self.gate_counts) != {"passed", "failed", "not_ready"} or not sum(
            self.gate_counts.values()
        ):
            raise ValueError("remediation_gate_count_inventory")
        expected = (
            "not_ready"
            if self.gate_counts["not_ready"]
            else "failed"
            if self.gate_counts["failed"]
            else "passed"
        )
        if self.quality_status != expected:
            raise ValueError("remediation_gate_status_mismatch")
        return self


class RemediationManifest(Contract):
    remediation_id: Annotated[str, Field(pattern=r"^forecast-remediation-sha256-[0-9a-f]{64}$")]
    descriptor: RemediationDescriptor
    receipts: dict[str, FileReceipt]
    generated_at: UtcTime
    forecast_model_status: Literal["not_ready"] = "not_ready"

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.remediation_id != "forecast-remediation-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ) or set(self.receipts) != set(self.descriptor.report_sha256):
            raise ValueError("remediation_identity_or_inventory_mismatch")
        if any(
            r.path != name or r.sha256 != self.descriptor.report_sha256[name]
            for name, r in self.receipts.items()
        ):
            raise ValueError("remediation_receipt_binding_mismatch")
        return self
