"""Train then calibrate then select; assess only a later untouched development role."""

from collections import Counter
from datetime import datetime
from typing import Any

from retailops_ai.stockout.split import key
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_qualification.contract import QualificationPolicy
from retailops_ai.stockout_training.contract import DEFAULT_POLICY, RiskPipeline
from retailops_ai.stockout_training.development import FAMILIES, VARIANTS, select_on_tune
from retailops_ai.stockout_training.evaluation import segmented_metrics
from retailops_ai.stockout_training.inputs import DevelopmentData, keys_sha256, labels_sha256
from retailops_ai.stockout_training.pipeline import fit_model, fit_sigmoid, predict


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def calibration_error(metric: dict[str, Any]) -> float:
    return float(
        round(
            sum(
                b["count"] * abs(b["mean_probability"] - b["observed_rate"])
                for b in metric["reliability"]
                if b["count"]
            )
            / metric["rows"],
            12,
        )
    )


def quality_gates(
    metrics: dict[str, Any],
    *,
    expected_categories: set[str],
    expected_locations: set[str],
    policy: QualificationPolicy,
) -> dict[str, Any]:
    required = {
        "all",
        *("category:" + v for v in expected_categories),
        *("stock_location:" + v for v in expected_locations),
        "historical_inventory_constraint:true",
    }
    gates: dict[str, Any] = {}
    for segment in sorted(required):
        result = metrics.get(segment)
        if result is None:
            gates[segment] = dict(status="not_evaluable", reason="required_segment_missing")
            continue
        enough = (
            result["rows"] >= policy.minimum_segment_rows
            and min(result["positives"], result["negatives"]) >= policy.minimum_segment_per_class
        )
        if not enough or result["status"] == "not_evaluable":
            gates[segment] = dict(
                status="not_evaluable",
                reason="insufficient_both_class_segment_support",
                rows=result["rows"],
                positives=result["positives"],
                negatives=result["negatives"],
            )
            continue
        ece = calibration_error(result)
        checks = dict(
            AP_above_no_skill=result["average_precision"] > result["prevalence"],
            brier_better_than_train_constant=result["brier"]
            < result["train_prevalence_constant_brier"],
            calibration_error_in_budget=ece <= policy.maximum_expected_calibration_error,
        )
        gates[segment] = dict(
            status="passed" if all(checks.values()) else "failed",
            checks=checks,
            expected_calibration_error=ece,
            rows=result["rows"],
            positives=result["positives"],
            negatives=result["negatives"],
        )
    universe_complete = (
        len(expected_categories) == policy.expected_category_count
        and len(expected_locations) == policy.expected_stock_location_count
    )
    return dict(
        status="passed"
        if universe_complete and all(g["status"] == "passed" for g in gates.values())
        else "not_ready",
        segments=gates,
        required_segment_universe_complete=universe_complete,
        policy_status=policy.quality_policy_status,
        final_test_qualified=False,
    )


def fit_qualification(
    data: DevelopmentData, membership: list[dict[str, Any]], policy: QualificationPolicy
) -> dict[str, Any]:
    """Internal fitter: the public builder fully replays and seals all source parents."""
    split = data.split_policy
    if (
        set(data.rows) != {"train", "tune", "calibration"}
        or set(data.outcomes) != set(data.rows)
        or not split.start_at < policy.base_fit_known_at
        or policy.calibration_fit_known_at > split.train_until
        or policy.calibration_start_at < policy.base_fit_known_at
    ):
        raise ValueError("stockout_qualification_development_only_chronology")
    indexed = {key(m): m for m in membership}
    if len(indexed) != len(membership):
        raise ValueError("stockout_qualification_duplicate_membership")
    base_rows, base_y, cal_rows, cal_y = [], [], [], []
    purged = 0
    for role, rows in data.rows.items():
        if len(rows) != len(data.outcomes[role]):
            raise ValueError("stockout_qualification_outcome_count")
        for row, y in zip(rows, data.outcomes[role], strict=True):
            m = indexed.get(key(row))
            if m is None or m["role"] != role or not m["eligible"] or y not in (0, 1):
                raise ValueError("stockout_qualification_membership_pin")
            known = timestamp(m["label_available_at"])
            end = timestamp(m["window_end_at"])
            origin = timestamp(row["as_of"])
            upper = {
                "train": split.train_until,
                "tune": split.tune_until,
                "calibration": split.calibration_until,
            }[role]
            lower = {
                "train": split.start_at,
                "tune": split.train_until,
                "calibration": split.tune_until,
            }[role]
            if not lower <= origin < upper or max(known, end) >= upper:
                raise ValueError("stockout_qualification_label_availability_or_role_leakage")
            if role != "train":
                continue
            if origin < policy.base_fit_known_at and max(known, end) < policy.base_fit_known_at:
                base_rows.append(row)
                base_y.append(y)
            elif (
                policy.calibration_start_at <= origin < policy.calibration_fit_known_at
                and max(known, end) < policy.calibration_fit_known_at
            ):
                cal_rows.append(row)
                cal_y.append(y)
            else:
                purged += 1
    if set(base_y) != {0, 1} or any(cal_y.count(c) < 10 for c in (0, 1)):
        raise ValueError("stockout_qualification_fitting_class_support")
    prevalence = sum(base_y) / len(base_y)
    pipelines: dict[str, RiskPipeline] = {}
    tune_results = {}
    for family in FAMILIES:
        for variant in VARIANTS:
            name = family + ":" + variant
            model = fit_model(
                base_rows,
                base_y,
                family=family,
                variant=variant,
                fit_known_at=policy.base_fit_known_at,
                policy=DEFAULT_POLICY,
            )
            sigmoid = fit_sigmoid(
                model,
                cal_rows,
                cal_y,
                fit_known_at=policy.calibration_fit_known_at,
                policy=DEFAULT_POLICY,
            )
            model = RiskPipeline.model_validate({**model.model_dump(), "sigmoid": sigmoid})
            pipelines[name] = model
            tune_results[name] = dict(
                tune=segmented_metrics(
                    data.rows["tune"],
                    data.outcomes["tune"],
                    list(predict(model, data.rows["tune"], calibrated=False)),
                    DEFAULT_POLICY,
                    train_prevalence=prevalence,
                )
            )
    # The family is selected before any validation probability or target is consumed.
    selected = select_on_tune(tune_results)
    validation: dict[str, Any] = {}
    for name, model in pipelines.items():
        validation[name] = {}
        for label, calibrated in (("raw", False), ("sigmoid", True)):
            validation[name][label] = segmented_metrics(
                data.rows["calibration"],
                data.outcomes["calibration"],
                list(predict(model, data.rows["calibration"], calibrated=calibrated)),
                DEFAULT_POLICY,
                train_prevalence=prevalence,
            )
    expected_categories = {r["category_id"] for rows in data.rows.values() for r in rows}
    expected_locations = {r["stock_location_id"] for rows in data.rows.values() for r in rows}
    gates = quality_gates(
        validation[selected]["sigmoid"],
        expected_categories=expected_categories,
        expected_locations=expected_locations,
        policy=policy,
    )
    model = pipelines[selected]
    monotone = model.sigmoid is not None and model.sigmoid.slope > 0
    if not monotone:
        gates["status"] = "not_ready"
    roles = dict(
        base_train=(base_rows, base_y),
        calibration_fit=(cal_rows, cal_y),
        tune=(data.rows["tune"], data.outcomes["tune"]),
        independent_validation=(data.rows["calibration"], data.outcomes["calibration"]),
    )
    role_keys = {r: {key(row) for row in rows} for r, (rows, _) in roles.items()}
    if any(role_keys[a] & role_keys[b] for a in roles for b in roles if a != b):
        raise ValueError("stockout_qualification_role_overlap")
    serialized = {n: m.model_dump(mode="json") for n, m in pipelines.items()}
    content = dict(
        pipelines=serialized,
        tune=tune_results,
        independent_validation=validation,
        selected_on_raw_tune=selected,
        selection_known_at=split.tune_until.isoformat().replace("+00:00", "Z"),
        gates=gates,
        selected_sigmoid_monotone=monotone,
        roles={
            r: dict(
                rows=len(rows),
                classes=dict(Counter(ys)),
                keys_sha256=keys_sha256(rows),
                labels_sha256=labels_sha256(rows, ys),
            )
            for r, (rows, ys) in roles.items()
        },
        purged_base_training_rows=purged,
        roles_disjoint=True,
        calibration_evaluation_out_of_sample=True,
        validation_never_fit_preprocessing_model_calibrator_or_selection=True,
        final_test_outcomes_evaluated=False,
        threshold_policy_ready=False,
        model_promoted=False,
        model_ready=False,
        limitations=[
            "synthetic_bounded_profile_not_full_ai_training",
            "overlapping_7d_windows_not_independent_episodes",
            "finite_segment_support_not_a_population_accuracy_guarantee",
            "development_quality_proposal_requires_approved_final_campaign",
        ],
    )
    descriptor = dict(
        role="stockout_independent_development_qualification",
        schema_version="1.0.0",
        parents=data.parents,
        policy=policy.model_dump(mode="json"),
        original_split_policy=split.model_dump(mode="json"),
        training_algorithm=DEFAULT_POLICY.model_dump(mode="json"),
        categorical_lineage_sha256=data.categorical_lineage_sha256,
        content_sha256=digest(content),
    )
    return dict(
        qualification_id="stockout-qualification-sha256-" + digest(descriptor),
        descriptor=descriptor,
        content=content,
    )
