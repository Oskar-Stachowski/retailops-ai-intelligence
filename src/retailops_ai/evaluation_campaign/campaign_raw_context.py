"""Join all raw trial predictions to an already audited complete Source context.

The public controller must verify durable context completion before launching
this worker. This bounded stream rechecks the sealed files and the full census;
neither its metadata nor the raw metrics authorize final access or qualification.
"""

import math
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, BinaryIO, Self

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
    CampaignContextBundleReceipt,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
    CampaignForecastTrialPrediction,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastKeyContext,
    CampaignForecastSegmentCensus,
)
from retailops_ai.evaluation_campaign.campaign_segment_metrics import RawTrialCriticalSegments
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, read_bytes, regular_file


def context_record(
    record: dict[str, Any], plan: CampaignForecastEvaluationPlan, population: dict[str, Any]
) -> tuple[CampaignContextBundleReceipt, CampaignForecastSegmentCensus]:
    """Bind a typed full census, never a caller-selected subset, to this evaluation."""
    if set(record) != {"receipt", "census"}:
        raise SnapshotError("campaign_raw_context_record_inventory_mismatch")
    receipt = CampaignContextBundleReceipt.model_validate_json(canonical_bytes(record["receipt"]))
    census = CampaignForecastSegmentCensus.model_validate_json(canonical_bytes(record["census"]))
    if (
        receipt.recipe.phase != plan.phase
        or receipt.recipe.role != plan.role
        or receipt.recipe.source_recipe_sha256 != plan.source_recipe_sha256
        or receipt.recipe.export_operation_id != plan.export_operation_id
        or receipt.scope.segment_policy_sha256 != plan.segment_policy_sha256
        or receipt.census_sha256 != census.content_sha256()
        or census.scope != receipt.scope
        or census.policy != receipt.recipe.segment_policy
        or census.context_trace_sha256 != receipt.context_trace_sha256
        or any(
            getattr(receipt, key) != population[key]
            for key in (
                "rows",
                "eligible_rows",
                "keys_sha256",
                "eligible_keys_sha256",
                "role_population_sha256",
            )
        )
        or any(
            getattr(census, key) != getattr(receipt, key)
            for key in ("rows", "eligible_rows", "keys_sha256", "eligible_keys_sha256")
        )
    ):
        raise SnapshotError("campaign_raw_context_full_population_or_policy_mismatch")
    return receipt, census


class RawContextPass:
    """One sorted context stream, with no contexts/actuals retained in a row list."""

    def __init__(
        self,
        bundle: Path,
        record: dict[str, Any],
        plan: CampaignForecastEvaluationPlan,
        population: dict[str, Any],
        trial: str,
    ) -> None:
        self.receipt, self.census = context_record(record, plan, population)
        self.bundle = bundle
        self._seal()
        observed = CampaignForecastSegmentCensus.model_validate_json(
            read_bytes(bundle, "census.json", min(plan.max_output_bytes, 16 * 1024**2))
        )
        if observed != self.census:
            raise SnapshotError("campaign_raw_context_census_file_mismatch")
        self.collector = RawTrialCriticalSegments(
            self.census, trial_tune_score_operation_id=trial, quality_policy=plan.quality_policy
        )
        self.context: AbstractContextManager[BinaryIO] | None = None
        self.stream: BinaryIO | None = None
        self.failed = self.complete = False

    def _seal(self) -> None:
        for name in ("contexts.jsonl", "census.json"):
            size, digest = file_hash(self.bundle, name)
            if (
                digest != self.receipt.artifact_files[name]
                or size > self.receipt.artifact_bytes
                or size > self.receipt.recipe.max_output_bytes
            ):
                raise SnapshotError("campaign_raw_context_file_checksum_mismatch")

    def __enter__(self) -> Self:
        self.context = regular_file(self.bundle, "contexts.jsonl")
        self.stream = self.context.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        if self.context is not None:
            self.context.__exit__(*args)
        self.stream = None

    def add(self, row: CampaignForecastTrialPrediction, actual: int | None) -> None:
        if self.stream is None or self.failed or self.complete:
            raise SnapshotError("campaign_raw_context_stream_unavailable")
        try:
            raw = self.stream.readline(self.receipt.recipe.max_record_bytes + 1)
            if not raw.endswith(b"\n") or len(raw) > self.receipt.recipe.max_record_bytes:
                raise SnapshotError("campaign_raw_context_record_missing_or_over_budget")
            context = CampaignForecastKeyContext.model_validate_json(raw)
            if raw != canonical_bytes(context.model_dump(mode="json")) + b"\n":
                raise SnapshotError("campaign_raw_context_record_noncanonical")
            self.collector.add(row, actual, context)
        except Exception:
            self.failed = True
            raise

    def finish(self) -> dict[str, Any]:
        if self.stream is None or self.failed or self.complete:
            raise SnapshotError("campaign_raw_context_stream_unavailable")
        try:
            if self.stream.read(1):
                raise SnapshotError("campaign_raw_context_extra_role_key")
            result = self.collector.finish()
            self._seal()
            self.complete = True
            return result
        except Exception:
            self.failed = True
            raise


def validate_raw_context_metrics(
    report: dict[str, Any],
    census: CampaignForecastSegmentCensus,
    plan: CampaignForecastEvaluationPlan,
    trial: str,
) -> None:
    """Require every declared group and all six models, including excluded/empty groups."""
    if (
        report.get("scope")
        != "full_declared_context_raw_trial_component_not_campaign_qualification"
        or report.get("trial_tune_score_operation_id") != trial
        or report.get("role") != plan.role
        or report.get("context_census_sha256") != census.content_sha256()
        or report.get("context_scope_sha256") != census.scope.content_sha256()
        or report.get("quality_policy_sha256") != plan.quality_policy.content_sha256()
        or report.get("critical_context_inventory_consumed") is not True
        or any(
            report.get(key) != getattr(census, key)
            for key in ("rows", "eligible_rows", "keys_sha256", "eligible_keys_sha256")
        )
        or any(
            report.get(key) is not False
            for key in (
                "audited_source_context_verified_by_this_component",
                "raw_learned_intervals_calibrated",
                "architecture_reselected",
                "quality_qualified",
                "final_access_authorized",
                "promotion_allowed",
                "stage_ready",
            )
        )
    ):
        raise SnapshotError("campaign_raw_context_metrics_scope_mismatch")
    groups = report.get("segments")
    if not isinstance(groups, list) or len(groups) != len(census.populations):
        raise SnapshotError("campaign_raw_context_metrics_group_inventory_mismatch")
    for group, population in zip(groups, census.populations, strict=True):
        expected = population.model_dump(mode="json")
        if (
            not isinstance(group, dict)
            or set(group) != {*expected, "models"}
            or any(group[key] != value for key, value in expected.items())
            or not isinstance(group["models"], dict)
            or set(group["models"]) != set(MODELS)
            or any(
                not isinstance(metrics, dict) or metrics.get("rows") != population.eligible_rows
                for metrics in group["models"].values()
            )
        ):
            raise SnapshotError("campaign_raw_context_metrics_group_population_mismatch")
        for metrics in group["models"].values():
            _metrics(metrics, population.eligible_rows)


def _metrics(metrics: dict[str, Any], eligible: int) -> None:
    if (
        set(metrics) != {"rows", "actual_sum", "mean", "median", "interval"}
        or type(metrics["rows"]) is not int
        or metrics["rows"] != eligible
        or type(metrics["actual_sum"]) is not int
        or metrics["actual_sum"] < 0
        or not eligible
        and metrics["actual_sum"] != 0
    ):
        raise SnapshotError("campaign_raw_context_metric_counts_invalid")
    for name in ("mean", "median", "interval"):
        head = metrics[name]
        values = (
            ("mean_score", "mean_width", "coverage", "width_to_mean_actual")
            if name == "interval"
            else ("mae", "mse", "bias_units", "wape", "normalized_bias", "zero_actual_excess_units")
        )
        denominator_values = (
            {"width_to_mean_actual"} if name == "interval" else {"wape", "normalized_bias"}
        )
        if (
            not isinstance(head, dict)
            or set(head) != {"complete", *values}
            or type(head["complete"]) is not bool
            or head["complete"]
            and not eligible
        ):
            raise SnapshotError("campaign_raw_context_metric_shape_invalid")
        for key in values:
            value = head[key]
            defined = head["complete"] and (
                key not in denominator_values or metrics["actual_sum"] > 0
            )
            if (
                defined
                and (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or key not in {"bias_units", "normalized_bias"}
                    and value < 0
                    or key == "coverage"
                    and value > 1
                )
                or not defined
                and value is not None
            ):
                raise SnapshotError("campaign_raw_context_metric_value_or_undefined_denominator")
