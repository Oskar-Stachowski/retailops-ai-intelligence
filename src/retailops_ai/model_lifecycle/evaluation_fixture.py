"""Explicit SQL/HTTP fixtures computed by the existing AI 04 metric accumulator."""

import json
from datetime import datetime

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.quality_metrics import SegmentAccumulator
from retailops_ai.model_lifecycle.evaluation_contracts import (
    METHODS,
    EvaluationDescriptor,
    EvaluationEvidence,
    EvaluationMetric,
)


def fixture(
    products: tuple[str, ...], now: datetime, *, salt: str, status: str = "not_ready"
) -> EvaluationEvidence:
    sha = canonical_sha256({"fixture": salt})
    metrics = []
    for role in ("development_holdout", "validation"):
        for method in sorted(METHODS):
            accumulator = SegmentAccumulator(0.9)
            for _ in products:
                accumulator.add(0, 1.0, (), None)
                accumulator.add(3, 2.0, (), None)
            segment = accumulator.result("pooled", role, method, "global", "all")
            metrics.append(
                EvaluationMetric.model_validate_json(
                    json.dumps(
                        segment.model_dump(mode="json", include=set(EvaluationMetric.model_fields))
                    )
                )
            )
    receipt = {"sha256": sha, "size_bytes": 1}
    raw = dict(
        purpose="synthetic_acceptance_only",
        evaluation_id="forecast-quality-sha256-" + sha,
        original_export_run_id="run-" + sha[:32],
        scope={
            "product_ids": sorted(products),
            "selling_location_ids": ["s-1"],
            "channels": ["store"],
        },
        quality_status=status,
        source_dataset_id="source-sha256-" + sha,
        curated_dataset_id="curated-sha256-" + sha,
        feature_set_id="features-sha256-" + sha,
        label_dataset_id="labels-sha256-" + sha,
        split_id="split-sha256-" + sha,
        backtest_id="forecast-backtest-sha256-" + sha,
        source_code_commit="a" * 40,
        ai_code_commit="b" * 40,
        dependency_lock_sha256=sha,
        generated_at=now.isoformat(),
        export_manifest=receipt,
        quality_manifest=receipt,
        segments_report=receipt,
        membership_content_sha256=sha,
        evaluation_membership_rows=4 * len(products),
        metrics=[m.model_dump(mode="json") for m in metrics],
    )
    d = EvaluationDescriptor.model_validate_json(json.dumps(raw))
    return EvaluationEvidence(
        descriptor=d, evidence_sha256=canonical_sha256(d.model_dump(mode="json"))
    )
