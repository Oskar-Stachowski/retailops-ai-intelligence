"""Verify sealed selected comparisons and separate paired sensitivity reports."""

import hashlib
import math
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_gates import assess_frozen_metrics
from retailops_ai.evaluation_campaign.campaign_raw_context import _metrics, context_record
from retailops_ai.evaluation_campaign.campaign_uncertainty_contract import (
    CampaignForecastUncertaintyPolicy,
    CampaignForecastUncertaintyReport,
    Metric,
)
from retailops_ai.source_snapshot.files import SnapshotError


def _delta(left: float | None, right: float | None) -> float | None:
    return left - right if left is not None and right is not None else None


def validate_selected_robustness(
    report: dict[str, Any],
    record: dict[str, Any],
    plan: CampaignForecastEvaluationPlan,
    population: dict[str, Any],
    policy: CampaignForecastUncertaintyPolicy,
    *,
    retained_median_baseline: bool,
) -> bool:
    """No original outcome read and no permission from a completeness header.

    Durable worker completion and artifact checksums authenticate the bootstrap
    output. Here every group, aggregate equation, scope, seed and method inventory
    is checked. This does not assert nominal statistical coverage.
    """
    receipt, census = context_record(record, plan, population)
    bindings = {
        "context_receipt_sha256": receipt.content_sha256(),
        "context_census_sha256": census.content_sha256(),
        "context_trace_sha256": census.context_trace_sha256,
        "uncertainty_policy_sha256": policy.content_sha256(),
        "all_declared_groups_consumed": True,
    }
    groups = report.get("groups")
    if (
        policy.content_sha256() != plan.uncertainty_policy_sha256
        or policy.nominal_interval_coverage != plan.quality_policy.nominal_coverage
        or census.rows > policy.max_rows
        or set(report) != {*bindings, "quality_qualified", "groups"}
        or any(report.get(key) != value for key, value in bindings.items())
        or report.get("all_declared_groups_consumed") is not True
        or type(report.get("quality_qualified")) is not bool
        or not isinstance(groups, list)
        or len(groups) != len(census.populations)
    ):
        raise SnapshotError("campaign_robustness_report_inventory_or_binding_mismatch")
    qualified = True
    for group, expected in zip(groups, census.populations, strict=True):
        fields = expected.model_dump(mode="json")
        if (
            not isinstance(group, dict)
            or set(group) != {*fields, "comparison", "uncertainty"}
            or any(group[key] != value for key, value in fields.items())
            or type(group["rows"]) is not int
            or type(group["eligible_rows"]) is not int
            or not isinstance(group["comparison"], dict)
        ):
            raise SnapshotError("campaign_robustness_group_population_mismatch")
        comparison = group["comparison"]
        candidate, baseline = comparison.get("candidate"), comparison.get("baseline")
        if not isinstance(candidate, dict) or not isinstance(baseline, dict):
            raise SnapshotError("campaign_robustness_group_metrics_missing")
        _metrics(candidate, expected.eligible_rows)
        _metrics(baseline, expected.eligible_rows)
        if candidate["actual_sum"] != baseline["actual_sum"]:
            raise SnapshotError("campaign_robustness_actual_population_mismatch")
        assessed = assess_frozen_metrics(
            candidate,
            baseline,
            total_rows=expected.rows,
            eligible_rows=expected.eligible_rows,
            dimension=expected.dimension,
            retained_median_baseline=retained_median_baseline,
            policy=plan.quality_policy,
        ) | {
            "scope": "frozen_campaign_segment_component_not_campaign_qualification",
            "role": plan.role if expected.rows else None,
            "frozen_configuration_sha256": plan.frozen_configuration_sha256
            if expected.rows
            else None,
            "reference_functionals": plan.quality_policy.reference_functionals,
        }
        if canonical_bytes(comparison) != canonical_bytes(assessed):
            raise SnapshotError("campaign_robustness_group_gate_mismatch")
        qualified = bool(qualified and comparison["status"] == "passed")
        paired = CampaignForecastUncertaintyReport.model_validate_json(
            canonical_bytes(group["uncertainty"])
        )
        scope = paired.scope
        if (
            paired.policy != policy
            or (paired.rows, paired.eligible_rows, paired.keys_sha256, paired.eligible_keys_sha256)
            != (
                expected.rows,
                expected.eligible_rows,
                expected.keys_sha256,
                expected.eligible_keys_sha256,
            )
            or scope.model_dump(mode="json")
            != {
                "data_seed": census.scope.data_seed,
                "role": plan.role,
                "dataset_id": census.scope.dataset_id,
                "source_recipe_sha256": plan.source_recipe_sha256,
                "frozen_configuration_sha256": plan.frozen_configuration_sha256,
                "scenario": expected.value if expected.dimension == "scenario" else "all",
                "dimension": expected.dimension,
                "value": expected.value,
            }
        ):
            raise SnapshotError("campaign_robustness_uncertainty_scope_mismatch")
        points: dict[Metric, float | None] = {
            "median_mae_delta": _delta(candidate["median"]["mae"], baseline["median"]["mae"]),
            "median_wape_delta": _delta(candidate["median"]["wape"], baseline["median"]["wape"]),
            "relative_median_mae_change": comparison["relative_median_mae_change"],
            "mean_mse_delta": _delta(candidate["mean"]["mse"], baseline["mean"]["mse"]),
            "normalized_mean_bias_delta": _delta(
                candidate["mean"]["normalized_bias"], baseline["mean"]["normalized_bias"]
            ),
            "interval_score_delta": _delta(
                candidate["interval"]["mean_score"], baseline["interval"]["mean_score"]
            ),
            "interval_coverage_delta": _delta(
                candidate["interval"]["coverage"], baseline["interval"]["coverage"]
            ),
        }
        visits = sum(
            method.clusters * policy.resamples
            for method, minimum in zip(
                paired.methods,
                (policy.minimum_eligible_time_blocks, policy.minimum_eligible_series_clusters),
                strict=True,
            )
            if method.eligible_clusters >= minimum
        )
        for method, minimum in zip(
            paired.methods,
            (policy.minimum_eligible_time_blocks, policy.minimum_eligible_series_clusters),
            strict=True,
        ):
            seed = int.from_bytes(
                hashlib.sha256(
                    canonical_bytes(
                        {
                            "scope": scope.model_dump(mode="json"),
                            "policy_sha256": policy.content_sha256(),
                            "method": method.method,
                        }
                    )
                ).digest()[:16],
                "big",
            )
            executed = (
                policy.resamples
                if method.eligible_clusters >= minimum
                and visits <= policy.max_resampled_cluster_visits
                else 0
            )
            if (
                method.actual_units != candidate["actual_sum"]
                or method.clusters > expected.rows
                or method.eligible_clusters > expected.eligible_rows
                or bool(method.clusters) != bool(expected.rows)
                or method.derived_resampling_seed != seed
                or method.resamples_executed != executed
            ):
                raise SnapshotError("campaign_robustness_uncertainty_method_mismatch")
            for name, point in points.items():
                observed = method.metrics[name].point_delta
                if (
                    (observed is None) != (point is None)
                    or observed is not None
                    and point is not None
                    and not math.isclose(observed, point, rel_tol=1e-10, abs_tol=1e-12)
                ):
                    raise SnapshotError("campaign_robustness_paired_point_equation_mismatch")
    if report["quality_qualified"] is not qualified:
        raise SnapshotError("campaign_robustness_qualification_mismatch")
    return bool(qualified)
