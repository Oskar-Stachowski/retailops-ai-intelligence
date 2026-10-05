"""Frozen numerical/sample gates over independently saved observation and episode reports."""

import json
from collections import Counter
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.anomaly_evaluation.contract import BusinessType, Metric
from retailops_ai.data_contracts.common import Contract
from retailops_ai.source_snapshot.files import json_sha256

TYPES: tuple[BusinessType, ...] = (
    "one_day_spike",
    "multi_day_spike",
    "sustained_drop",
    "return_spike",
    "inventory_censored_episode",
)


class QualityPolicy(Contract):
    version: Literal["anomaly-portfolio-quality-1.0.0"] = "anomaly-portfolio-quality-1.0.0"
    minimum_precision: Annotated[float, Field(gt=0, le=1)]
    minimum_recall: Annotated[float, Field(gt=0, le=1)]
    maximum_false_alerts_per_1000: Annotated[float, Field(ge=0, le=1000)]
    minimum_high_severity_precision: Annotated[float, Field(gt=0, le=1)]
    minimum_episode_recall: Annotated[float, Field(gt=0, le=1)]
    minimum_episode_recall_per_type: Annotated[float, Field(gt=0, le=1)] = 0.5
    minimum_evaluable_coverage: Annotated[float, Field(gt=0, le=1)]
    minimum_clean_per_case: Annotated[int, Field(ge=1, le=10000)]
    minimum_episodes_per_type: Annotated[int, Field(ge=3, le=100)] = 3
    minimum_positive_observations_per_type: Annotated[int, Field(ge=3, le=10000)] = 3
    source_seeds: tuple[Literal[42, 137, 2026], ...] = (42, 137, 2026)
    source_scenarios: tuple[Literal["demand", "physical"], ...] = ("demand", "physical")
    required_types: tuple[BusinessType, ...] = TYPES
    segment_policy: Literal[
        "event_type_currency_validity_and_clean_sample_report_quality_per_type"
    ] = "event_type_currency_validity_and_clean_sample_report_quality_per_type"
    null_required_metric: Literal["blocks_qualification"] = "blocks_qualification"
    qualification_scope: Literal["synthetic_ai_07_portfolio_v1", "synthetic_ai_07_portfolio_v2"] = (
        "synthetic_ai_07_portfolio_v1"
    )

    @model_validator(mode="after")
    def frozen_inventory(self) -> Self:
        if (
            self.source_seeds != (42, 137, 2026)
            or self.source_scenarios != ("demand", "physical")
            or self.required_types != TYPES
        ):
            raise ValueError("anomaly_quality_frozen_inventory")
        return self


def metric(value: dict[str, Any]) -> Metric:
    return Metric.model_validate_json(json.dumps(value))


COUNTS = ("true_positive", "false_positive", "false_negative", "true_negative")


def reconcile_observation(obs: dict[str, Any]) -> None:
    if (
        any(type(obs[k]) is not int or obs[k] < 0 for k in (*COUNTS, "n_requested", "n_evaluable"))
        or obs["n_evaluable"] != sum(obs[k] for k in COUNTS)
        or obs["n_evaluable"] > obs["n_requested"]
        or obs["positive_observations"] != obs["true_positive"] + obs["false_negative"]
        or obs["clean_observations"] != obs["false_positive"] + obs["true_negative"]
        or obs["predicted_positive"] != obs["true_positive"] + obs["false_positive"]
    ):
        raise ValueError("anomaly_quality_observation_reconciliation")
    for name in (
        "precision",
        "recall",
        "false_alerts_per_1000",
        "high_severity_precision",
        "average_precision",
    ):
        checked = metric(obs[name])
        if checked.n_total != obs["n_requested"] or checked.n_evaluable != obs["n_evaluable"]:
            raise ValueError("anomaly_quality_metric_coverage")
    for name, numerator, denominator, multiplier in (
        ("precision", obs["true_positive"], obs["predicted_positive"], 1),
        ("recall", obs["true_positive"], obs["positive_observations"], 1),
        ("false_alerts_per_1000", obs["false_positive"], obs["clean_observations"], 1000),
    ):
        checked = metric(obs[name])
        if (
            checked.value != (multiplier * numerator / denominator if denominator else None)
            or checked.numerator != numerator
            or checked.denominator != denominator
        ):
            raise ValueError("anomaly_quality_metric_counts")
    high = metric(obs["high_severity_precision"])
    if (
        high.numerator is None
        or high.denominator is None
        or high.numerator > obs["true_positive"]
        or high.denominator > obs["predicted_positive"]
        or high.value != (high.numerator / high.denominator if high.denominator else None)
    ):
        raise ValueError("anomaly_quality_high_severity_counts")


def combine(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not cases or len(cases) > 6:
        raise ValueError("anomaly_quality_case_budget")
    counters: Counter[str] = Counter()
    positive_types: Counter[str] = Counter()
    episode_types: Counter[str] = Counter()
    detected_types: Counter[str] = Counter()
    segments: dict[str, Counter[str]] = {}
    delays: list[dict[str, Any]] = []
    inventory = set()
    families = set()
    models = set()
    for case in cases:
        report = case["report"]
        desc = report["descriptor"]
        if report["evaluation_id"] != "anomaly-evaluation-sha256-" + json_sha256(desc):
            raise ValueError("anomaly_quality_evaluation_identity")
        if (
            case["source_dataset_id"] != desc["source_dataset_id"]
            or (case["seed"], case["scenario"]) in inventory
        ):
            raise ValueError("anomaly_quality_duplicate_case_or_source")
        inventory.add((case["seed"], case["scenario"]))
        families.add(desc["family"])
        models.add(desc["detector_id"])
        obs = desc["observation"]
        coverage = desc["coverage"]
        episode = desc["episode"]
        counts = COUNTS
        reconcile_observation(obs)
        if (
            any(
                type(coverage[k]) is not int or coverage[k] < 0
                for k in (
                    "requested",
                    "evaluable",
                    "unknown_truth",
                    "immature_truth",
                    "insufficient_data",
                    "input_insufficient_data_total",
                )
            )
            or coverage["requested"] != obs["n_requested"]
            or coverage["evaluable"] != obs["n_evaluable"]
            or sum(
                coverage[k]
                for k in ("evaluable", "unknown_truth", "immature_truth", "insufficient_data")
            )
            != coverage["requested"]
            or not coverage["insufficient_data"]
            <= coverage["input_insufficient_data_total"]
            <= coverage["requested"]
        ):
            raise ValueError("anomaly_quality_coverage_reconciliation")
        segment_values = list(desc["per_segment"].values())
        for part in segment_values:
            reconcile_observation(part)
        if any(
            sum(part[k] for part in segment_values) != obs[k]
            for k in (*COUNTS, "n_requested", "n_evaluable")
        ):
            raise ValueError("anomaly_quality_segment_reconciliation")
        for part in desc["per_type"].values():
            reconcile_observation(part)
        details = episode["details"]
        if episode["n_evaluable"] != len(details) or episode["n_detected"] != sum(
            d["detected"] for d in details
        ):
            raise ValueError("anomaly_quality_episode_reconciliation")
        if (
            len({d["episode_id"] for d in details}) != len(details)
            or any(
                type(d["detected"]) is not bool
                or type(d["alert_count"]) is not int
                or d["alert_count"] < 0
                or d["detected"] != (d["alert_count"] > 0)
                or d["repeat_alert_count"] != max(0, d["alert_count"] - 1)
                for d in details
            )
            or episode["repeat_alert_count"] != sum(d["repeat_alert_count"] for d in details)
        ):
            raise ValueError("anomaly_quality_episode_details")
        recall = metric(episode["recall"])
        if (
            recall.numerator != episode["n_detected"]
            or recall.denominator != len(details)
            or recall.value != (episode["n_detected"] / len(details) if details else None)
        ):
            raise ValueError("anomaly_quality_episode_recall")
        for kind, part in episode["per_type"].items():
            typed = [d for d in details if d["business_type"] == kind]
            r = metric(part)
            if r.denominator != len(typed) or r.numerator != sum(d["detected"] for d in typed):
                raise ValueError("anomaly_quality_episode_type_counts")
        for k in counts:
            counters[k] += obs[k]
        counters.update(
            requested=coverage["requested"],
            evaluable=coverage["evaluable"],
            unknown_truth=coverage["unknown_truth"],
            immature_truth=coverage["immature_truth"],
            insufficient_data=coverage["insufficient_data"],
            input_insufficient_data_total=coverage["input_insufficient_data_total"],
            episodes=len(details),
            detected_episodes=episode["n_detected"],
            repeat_alerts=episode["repeat_alert_count"],
        )
        high = metric(obs["high_severity_precision"])
        counters["high_true"] += high.numerator or 0
        counters["high_alerts"] += high.denominator or 0
        for kind, values in desc["per_type"].items():
            positive_types[kind] += values["positive_observations"]
        for d in details:
            episode_types[d["business_type"]] += 1
            detected_types[d["business_type"]] += d["detected"]
            delays.append(d)
        for segment, values in desc["per_segment"].items():
            part = segments.setdefault(segment, Counter())
            for k in counts:
                part[k] += values[k]
            part["evaluable"] += values["n_evaluable"]
            part["requested"] += values["n_requested"]
    if len(families) != 1 or len(models) != 1:
        raise ValueError("anomaly_quality_one_saved_model_required")

    def fraction(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    clean = counters["false_positive"] + counters["true_negative"]
    positive = counters["true_positive"] + counters["false_negative"]
    values = {
        "precision": fraction(
            counters["true_positive"], counters["true_positive"] + counters["false_positive"]
        ),
        "recall": fraction(counters["true_positive"], positive),
        "false_alerts_per_1000": 1000 * counters["false_positive"] / clean if clean else None,
        "high_severity_precision": fraction(counters["high_true"], counters["high_alerts"]),
        "episode_recall": fraction(counters["detected_episodes"], counters["episodes"]),
        "evaluable_coverage": fraction(counters["evaluable"], counters["requested"]),
    }
    return {
        "version": "anomaly-portfolio-summary-1.0.0",
        "detector_id": next(iter(models)),
        "family": next(iter(families)),
        "cases": cases,
        "counts": dict(counters),
        "metrics": values,
        "positive_observations_per_type": dict(positive_types),
        "episodes_per_type": dict(episode_types),
        "detected_episodes_per_type": dict(detected_types),
        "per_segment": {k: dict(v) for k, v in segments.items()},
        "delay_and_repeat_details": delays,
    }


def assess(cases: list[dict[str, Any]], policy: QualityPolicy) -> dict[str, Any]:
    policy = QualityPolicy.model_validate_json(policy.model_dump_json())
    summary = combine(cases)
    checks = []

    def check(name: str, value: Any, threshold: Any, passed: bool) -> None:
        checks.append(
            {
                "check_id": name,
                "status": "passed" if passed else "not_evaluable" if value is None else "failed",
                "value": value,
                "threshold": threshold,
                "severity": "hard",
            }
        )

    wanted = {
        (seed, scenario) for seed in policy.source_seeds for scenario in policy.source_scenarios
    }
    present = {(c["seed"], c["scenario"]) for c in cases}
    check("all_frozen_seeds_scenarios", len(present), len(wanted), present == wanted)
    for name, threshold, lower in (
        ("precision", policy.minimum_precision, True),
        ("recall", policy.minimum_recall, True),
        ("false_alerts_per_1000", policy.maximum_false_alerts_per_1000, False),
        ("high_severity_precision", policy.minimum_high_severity_precision, True),
        ("episode_recall", policy.minimum_episode_recall, True),
        ("evaluable_coverage", policy.minimum_evaluable_coverage, True),
    ):
        value = summary["metrics"][name]
        check(
            name,
            value,
            threshold,
            value is not None and (value >= threshold if lower else value <= threshold),
        )
    for kind in policy.required_types:
        count = summary["episodes_per_type"].get(kind, 0)
        check(
            "episode_sample/" + kind,
            count,
            policy.minimum_episodes_per_type,
            count >= policy.minimum_episodes_per_type,
        )
        detected = summary["detected_episodes_per_type"].get(kind, 0)
        recall = detected / count if count else None
        check(
            "episode_recall/" + kind,
            recall,
            policy.minimum_episode_recall_per_type,
            recall is not None and recall >= policy.minimum_episode_recall_per_type,
        )
        count = summary["positive_observations_per_type"].get(kind, 0)
        check(
            "positive_observation_sample/" + kind,
            count,
            policy.minimum_positive_observations_per_type,
            count >= policy.minimum_positive_observations_per_type,
        )
    for segment, counts in summary["per_segment"].items():
        clean = counts["false_positive"] + counts["true_negative"]
        value = 1000 * counts["false_positive"] / clean if clean else None
        check(
            "segment_false_alerts/" + segment,
            value,
            policy.maximum_false_alerts_per_1000,
            value is not None and value <= policy.maximum_false_alerts_per_1000,
        )
        check(
            "segment_clean_sample/" + segment,
            clean,
            policy.minimum_clean_per_case,
            clean >= policy.minimum_clean_per_case,
        )
    for case in cases:
        desc = case["report"]["descriptor"]
        obs = desc["observation"]
        name = f"{case['seed']}/{case['scenario']}"
        check(
            "clean_sample/" + name,
            obs["clean_observations"],
            policy.minimum_clean_per_case,
            obs["clean_observations"] >= policy.minimum_clean_per_case,
        )
        for metric_name in ("precision", "recall", "false_alerts_per_1000", "average_precision"):
            m = metric(obs[metric_name])
            check(
                "metric_validity/" + name + "/" + metric_name,
                m.value,
                "evaluable",
                m.status == "evaluable",
            )
    status = "passed" if all(c["status"] == "passed" for c in checks) else "not_ready"
    descriptor = {
        "version": policy.version,
        "policy": policy.model_dump(mode="json"),
        "summary": summary,
        "checks": checks,
        "status": status,
    }
    return {
        "quality_id": "anomaly-quality-sha256-" + json_sha256(descriptor),
        "descriptor": descriptor,
    }
