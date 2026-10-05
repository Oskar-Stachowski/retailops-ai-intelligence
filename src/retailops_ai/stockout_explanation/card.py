"""Replay a development parent before building its immutable explanatory card."""

import hashlib
import json
from importlib.resources import files
from typing import Any

from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.feature_contract import FeaturePoint
from retailops_ai.stockout.split import key
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_explanation.diagnostics import (
    MAX_CARD_BYTES,
    ExplanationPolicy,
    coefficients,
    factual_context,
    permutation_importance,
)
from retailops_ai.stockout_training.contract import RiskPipeline, TrainingPolicy
from retailops_ai.stockout_training.development import build_development, implementation
from retailops_ai.stockout_training.inputs import DevelopmentData


def explanation_implementation() -> dict[str, Any]:
    code = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in sorted(files("retailops_ai.stockout_explanation").iterdir(), key=lambda r: r.name)
        if r.is_file() and r.name.endswith(".py")
    }
    return dict(explanation_code=code, code_sha256=digest(code), training=implementation())


def build_card(
    data: DevelopmentData,
    development: dict[str, Any],
    features: dict[str, Any],
    *,
    policy: ExplanationPolicy | None = None,
) -> dict[str, Any]:
    """Features/data must have fully verified parents; the training bundle gets a full replay here."""
    policy = policy or ExplanationPolicy()
    training_policy = TrainingPolicy.model_validate(development["descriptor"]["policy"])
    if development != build_development(data, policy=training_policy):
        raise ValueError("stockout_card_development_full_replay_mismatch")
    if features["feature_dataset_id"] != data.parents["feature_dataset_id"]:
        raise ValueError("stockout_card_feature_parent_mismatch")
    pipelines = {
        name: RiskPipeline.model_validate_json(json.dumps(p))
        for name, p in development["pipelines"].items()
    }
    selected = development["descriptor"]["selection"]["name"]
    indexed = {key(p): p for p in features["points"]}
    context = [
        factual_context(FeaturePoint.model_validate_json(json.dumps(indexed[key(r)])))
        for r in data.rows["tune"]
    ]
    category_segments = {
        k: v
        for k, v in development["results"][selected]["tune"].items()
        if k.startswith("category:")
    }
    content = dict(
        purpose="new_incident_stockout_within_7d_product_physical_stock_location_as_of",
        intended_use="synthetic_development_review_only",
        prohibited_use="production_decisions_or_quality_claim_without_further_qualification",
        selected_provisional=development["descriptor"]["selection"],
        coverage=development["report"]["coverage"],
        development_classes={
            r: {"0": ys.count(0), "1": ys.count(1)} for r, ys in data.outcomes.items()
        },
        split_policy=development["descriptor"]["split_policy"],
        training_policy=development["descriptor"]["policy"],
        selected_metrics=development["results"][selected],
        category_segments=dict(
            total=len(category_segments),
            not_evaluable=sum(v["status"] == "not_evaluable" for v in category_segments.values()),
        ),
        coefficient_tables={
            name: coefficients(p)
            for name, p in pipelines.items()
            if p.family == "logistic_regression"
        },
        selected_tune_permutation_importance=permutation_importance(
            pipelines[selected], data.rows["tune"], data.outcomes["tune"], policy
        ),
        local_factual_context=context,
        limits=[
            "102_day_temporal_smoke_not_full_ai_training_acceptance",
            "synthetic_data_not_real_retail_validation",
            "overlapping_7d_windows_not_independent_incidents",
            "one_class_or_small_segments_do_not_pass_quality_gates",
            "calibration_sigmoid_metrics_are_in_sample_fit_diagnostics",
            "illustrative_capacity_and_FP_FN_costs_not_approved_business_policy",
            "global_upstream_coverage_incomplete_campaign_development_complete",
            "new_serving_requires_eligibility_staleness_and_null_probability_gates",
        ],
        readiness=dict(
            model_card_development_complete=True,
            final_test_outcomes_evaluated=False,
            calibration_generalization_evaluated=False,
            threshold_policy_ready=False,
            production_promoted=False,
            full_training_profile_measured=False,
            model_ready=False,
        ),
    )
    descriptor = dict(
        schema_version="1.0.0",
        role="stockout_development_model_card",
        parents={**data.parents, "development_id": development["development_id"]},
        selected_model_id=development["descriptor"]["selection"]["model_id"],
        policy=policy.model_dump(mode="json"),
        implementation=explanation_implementation(),
        content_sha256=digest(content),
    )
    document = dict(
        card_id="stockout-card-sha256-" + digest(descriptor), descriptor=descriptor, content=content
    )
    if len(canonical_json(document)) + 1 > MAX_CARD_BYTES:
        raise ValueError("stockout_card_output_byte_limit")
    return document
