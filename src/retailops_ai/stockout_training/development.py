"""Frozen six-way development comparison; selection never consumes calibration or test."""

import hashlib
import platform
from importlib.metadata import version
from importlib.resources import files
from typing import Any

from retailops_ai.forecasting import model_contract, model_trees
from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.feature_dataset import feature_implementation
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_training.contract import (
    DEFAULT_POLICY,
    MAX_BYTES,
    Family,
    RiskPipeline,
    TrainingPolicy,
    Variant,
)
from retailops_ai.stockout_training.evaluation import segmented_metrics
from retailops_ai.stockout_training.inputs import DevelopmentData, keys_sha256, labels_sha256
from retailops_ai.stockout_training.pipeline import (
    fit_model,
    fit_sigmoid,
    probabilities,
    raw_scores,
)

FAMILIES: tuple[Family, ...] = ("logistic_regression", "hist_gradient_boosting")
VARIANTS: tuple[Variant, ...] = ("without_upstream", "with_upstream", "raw_sales_only")


def implementation() -> dict[str, Any]:
    code = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in sorted(files("retailops_ai.stockout_training").iterdir(), key=lambda r: r.name)
        if r.is_file() and r.name.endswith(".py")
    }
    for module in (model_contract, model_trees):
        resource = files("retailops_ai.forecasting").joinpath(
            module.__name__.rsplit(".", 1)[-1] + ".py"
        )
        code[module.__name__] = hashlib.sha256(resource.read_bytes()).hexdigest()
    return dict(
        training_code=code,
        training_code_sha256=digest(code),
        preparation=feature_implementation(),
        python=platform.python_version(),
        versions={n: version(n) for n in ("scikit-learn", "numpy", "scipy", "threadpoolctl")},
    )


def select_on_tune(results: dict[str, dict[str, Any]]) -> str:
    eligible = [k for k in results if not k.endswith(":raw_sales_only")]
    if any(results[k]["tune"]["all"]["average_precision"] is None for k in eligible):
        raise ValueError("stockout_selection_tune_not_evaluable")
    return min(
        eligible,
        key=lambda k: (
            -results[k]["tune"]["all"]["average_precision"],
            results[k]["tune"]["all"]["brier"],
            not k.startswith("logistic_regression:"),
            not k.endswith(":without_upstream"),
            k,
        ),
    )


def build_development(
    data: DevelopmentData, *, policy: TrainingPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    # Defense in depth: a caller cannot introduce a final-test matrix under another role.
    if set(data.rows) != {"train", "tune", "calibration"} or set(data.outcomes) != set(data.rows):
        raise ValueError("stockout_development_roles_only")
    for role, lower, upper in (
        ("train", data.split_policy.start_at, data.split_policy.train_until),
        ("tune", data.split_policy.train_until, data.split_policy.tune_until),
        ("calibration", data.split_policy.tune_until, data.split_policy.calibration_until),
    ):
        from datetime import datetime

        if (
            not data.rows[role]
            or len(data.rows[role]) != len(data.outcomes[role])
            or set(data.outcomes[role]) != {0, 1}
            or len({(r["product_id"], r["stock_location_id"], r["as_of"]) for r in data.rows[role]})
            != len(data.rows[role])
            or any(not lower <= datetime.fromisoformat(r["as_of"]) < upper for r in data.rows[role])
        ):
            raise ValueError("stockout_development_role_window_or_labels_invalid")
    train_prevalence = sum(data.outcomes["train"]) / len(data.outcomes["train"])
    models: dict[str, RiskPipeline] = {}
    results: dict[str, dict[str, Any]] = {}
    for family in FAMILIES:
        for variant in VARIANTS:
            name = family + ":" + variant
            model = fit_model(
                data.rows["train"],
                data.outcomes["train"],
                family=family,
                variant=variant,
                fit_known_at=data.split_policy.train_until,
                policy=policy,
            )
            scores = [float(v) for v in probabilities(raw_scores(model, data.rows["tune"]))]
            models[name] = model
            results[name] = dict(
                tune=segmented_metrics(
                    data.rows["tune"],
                    data.outcomes["tune"],
                    scores,
                    policy,
                    train_prevalence=train_prevalence,
                ),
                tune_keys_sha256=keys_sha256(data.rows["tune"]),
            )
    # The chosen family/variant is frozen before reading any calibration target here.
    selected = select_on_tune(results)
    for name, model in models.items():
        calibrator = fit_sigmoid(
            model,
            data.rows["calibration"],
            data.outcomes["calibration"],
            fit_known_at=data.split_policy.calibration_until,
            policy=policy,
        )
        models[name] = RiskPipeline.model_validate({**model.model_dump(), "sigmoid": calibrator})
        raw = raw_scores(model, data.rows["calibration"])
        results[name]["calibration_raw_diagnostic"] = segmented_metrics(
            data.rows["calibration"],
            data.outcomes["calibration"],
            [float(v) for v in probabilities(raw)],
            policy,
            train_prevalence=train_prevalence,
        )
        results[name]["calibration_sigmoid_in_sample"] = (
            segmented_metrics(
                data.rows["calibration"],
                data.outcomes["calibration"],
                [float(v) for v in probabilities(raw * calibrator.slope + calibrator.intercept)],
                policy,
                train_prevalence=train_prevalence,
            )
            if calibrator is not None
            else None
        )
        results[name]["calibration_status"] = "fitted" if calibrator else "not_evaluable"
    pipelines = {name: model.model_dump(mode="json") for name, model in models.items()}
    model_ids = {name: "risk-model-sha256-" + digest(p) for name, p in pipelines.items()}
    descriptor = dict(
        schema_version="1.0.0",
        role="stockout_development_comparison",
        parents=data.parents,
        split_policy=data.split_policy.model_dump(mode="json"),
        policy=policy.model_dump(mode="json"),
        implementation=implementation(),
        categorical_lineage_sha256=data.categorical_lineage_sha256,
        development_keys={r: keys_sha256(rows) for r, rows in data.rows.items()},
        development_labels={
            r: labels_sha256(rows, data.outcomes[r]) for r, rows in data.rows.items()
        },
        model_ids=model_ids,
        pipelines_sha256=digest(pipelines),
        results_sha256=digest(results),
        selection=dict(name=selected, model_id=model_ids[selected], role="tune", provisional=True),
    )
    document = dict(
        development_id="development-sha256-" + digest(descriptor),
        descriptor=descriptor,
        pipelines=pipelines,
        results=results,
        report=dict(
            coverage=data.coverage,
            models_compared=len(models),
            common_development_keys_identical=True,
            model_specific_drops=0,
            upstream_campaign_development_coverage_complete=all(
                data.coverage[r]["eligible"] == data.coverage[r]["forecast_available"]
                for r in ("train", "tune", "calibration")
            ),
            raw_global_upstream_forecast_ready=data.coverage["raw_global_upstream_forecast_ready"],
            selected_on_tune=selected,
            selection_provisional=True,
            calibration_generalization="not_evaluated_fit_diagnostics_only",
            sigmoid_fitted_models=sum(m.sigmoid is not None for m in models.values()),
            sigmoid_monotone_models=sum(
                m.sigmoid is not None and m.sigmoid.slope > 0 for m in models.values()
            ),
            final_test_outcomes_evaluated=False,
            thresholds_fitted=False,
            threshold_policy_ready=False,
            production_promoted=False,
            full_training_profile_measured=False,
            model_ready=False,
            caution="synthetic_temporal_smoke_overlapping_7d_windows_not_independent_episodes",
        ),
    )
    if len(canonical_json(document)) + 1 > MAX_BYTES:
        raise ValueError("stockout_development_output_byte_limit")
    return document
