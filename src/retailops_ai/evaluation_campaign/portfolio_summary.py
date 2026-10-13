"""Equal-seed summary and the predeclared TF decision; never a repository ready gate."""

import math
from typing import Any

from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.source_snapshot.files import SnapshotError

SEEDS = (42, 137, 2026)


def aggregate(
    reports: dict[int, dict[str, Any]], *, freeze_sha256: str, groups: dict[str, list[str]]
) -> dict[str, Any]:
    if set(reports) != set(SEEDS) or any(
        report.get("seed") != seed
        or report.get("freeze_sha256") != freeze_sha256
        or report.get("scope") != "frozen_portfolio_final_per_seed"
        or report.get("final_test_opened") is not True
        or set(report["models"]) != set(MODELS)
        or report["rows_per_model"] <= 0
        for seed, report in reports.items()
    ):
        raise SnapshotError("portfolio_summary_requires_all_three_matching_final_seeds")

    def summary(values: list[float | None]) -> dict[str, Any]:
        if any(
            v is not None and (type(v) not in {int, float} or not math.isfinite(v)) for v in values
        ):
            raise SnapshotError("portfolio_summary_nonfinite_metric")
        available = [v for v in values if v is not None]
        complete = len(available) == len(SEEDS)
        mean = math.fsum(v / 3 for v in available) if complete else None
        return {
            "per_seed": dict(zip(map(str, SEEDS), values, strict=True)),
            "equal_seed_mean": mean,
            "minimum": min(available) if complete else None,
            "maximum": max(available) if complete else None,
            "population_stddev_across_three_fixed_seeds": math.sqrt(
                math.fsum((v - mean) ** 2 for v in available) / 3
            )
            if mean is not None
            else None,
            "complete": complete,
        }

    models = {}
    for name in MODELS:
        components = {}
        for component, metrics in {
            "mean": ("mae", "mse", "bias_units", "normalized_bias", "wape"),
            "median": ("mae", "mse", "bias_units", "normalized_bias", "wape"),
            "interval": ("coverage", "mean_width", "mean_score"),
        }.items():
            components[component] = {
                metric: summary(
                    [
                        reports[s]["models"][name]["global"]["candidate"][component][metric]
                        for s in SEEDS
                    ]
                )
                for metric in metrics
            }
        components["point_deltas"] = {
            metric: summary(
                [reports[s]["models"][name]["global"]["point_deltas"][metric] for s in SEEDS]
            )
            for metric in (
                "mean_mae",
                "mean_mse",
                "mean_bias_units",
                "median_mae",
                "median_mse",
                "median_bias_units",
            )
        }
        models[name] = components
    reasons = []
    for seed in SEEDS:
        candidate = reports[seed]["models"]["tensorflow"]
        checked = [("global", candidate["global"])]
        for dimension, expected in groups.items():
            if not set(expected).issubset(candidate["segments"].get(dimension, {})):
                raise SnapshotError("portfolio_summary_required_group_missing")
            checked.extend(
                (dimension + ":" + value, candidate["segments"][dimension][value])
                for value in expected
            )
        for group, result in checked:
            if result["status"] != "passed":
                reasons.append(
                    {
                        "seed": seed,
                        "group": group,
                        "status": result["status"],
                        "eligible_rows": result["eligible_rows"],
                        "not_ready_reasons": result["not_ready_reasons"],
                        "failed_reasons": result["failed_reasons"],
                    }
                )
    return {
        "version": "ai09-portfolio-final-summary-1.0.0",
        "freeze_sha256": freeze_sha256,
        "seed_weights": {str(s): 1 / 3 for s in SEEDS},
        "models": models,
        "rows_per_seed": {str(s): reports[s]["rows_per_model"] for s in SEEDS},
        "eligible_rows_per_seed": {
            str(s): reports[s]["models"]["tensorflow"]["global"]["eligible_rows"] for s in SEEDS
        },
        "tensorflow_decision": "reject_for_new_promotion_retain_existing_portfolio"
        if reasons
        else "accept_for_further_consideration",
        "decision_reasons": reasons,
        "new_model_selection_or_tuning": False,
        "promotion_allowed": False,
        "stage_ready_granted_by_summary": False,
        "uncertainty_scope": "descriptive_spread_of_three_fixed_seeds_not_a_confidence_interval",
    }
