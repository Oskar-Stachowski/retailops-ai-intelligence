"""Replay saved point metrics with AI 04's accumulator; never fit or recalibrate a model."""

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from retailops_ai.forecasting.backtest import pool_metrics
from retailops_ai.forecasting.evaluation import MAX_LINE, MetricAccumulator
from retailops_ai.forecasting.evaluation_contract import BASELINES, BaselinePrediction
from retailops_ai.forecasting.manifest_contract import LabelPoint, Membership
from retailops_ai.forecasting.manifest_io import iter_table, key
from retailops_ai.forecasting.model_contract import LEARNED_NAMES, ModelPrediction
from retailops_ai.forecasting.models import load_comparison
from retailops_ai.forecasting.quality_contract import SegmentMetric
from retailops_ai.forecasting.splits import load_split
from retailops_ai.model_lifecycle.evaluation_contracts import (
    METHODS,
    EvaluationMetric,
    EvaluationScope,
)
from retailops_ai.source_snapshot.files import decode_json, read_bytes, regular_file

MAX_MEMBERS = 100000


def replay(root: Path) -> tuple[EvaluationScope, int, str, tuple[EvaluationMetric, ...]]:
    backtest = root / "backtest"
    split = load_split(backtest / "split")
    comparison = load_comparison(backtest / "comparison")
    choices = {s.fold: s for s in comparison.descriptor.selections}
    if any(
        s.status != "selected" or s.selected is None or s.baseline is None for s in choices.values()
    ):
        raise ValueError("evaluation_requires_complete_recorded_fold_selections")
    members: dict[bytes, Membership] = {}
    for row in iter_table(
        backtest / "split", "memberships", split.tables["memberships"], Counter()
    ):
        if isinstance(row, Membership) and row.role in {"validation", "development_holdout"}:
            if len(members) >= MAX_MEMBERS:
                raise ValueError("evaluation_membership_resource_limit")
            members[key(row)] = row
    labels = {}
    for row in iter_table(backtest / "split", "labels", split.tables["labels"], Counter()):
        if isinstance(row, LabelPoint) and key(row) in members:
            labels[key(row)] = row
    if set(labels) != set(members):
        raise ValueError("evaluation_labels_missing")
    accumulators = {
        (fold, role, method): MetricAccumulator()
        for fold in choices
        for role in ("validation", "development_holdout")
        for method in METHODS
    }
    masks: dict[bytes, int] = defaultdict(int)
    names = (*BASELINES, *LEARNED_NAMES)
    with regular_file(backtest / "comparison", "predictions.jsonl") as stream:
        while line := stream.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE or not line.endswith(b"\n"):
                raise ValueError("evaluation_prediction_line_limit")
            payload = decode_json(line)
            p = (
                ModelPrediction.model_validate_json(line)
                if payload["model"] in LEARNED_NAMES
                else BaselinePrediction.model_validate_json(line)
            )
            if p.role not in {"validation", "development_holdout"}:
                continue
            identity = key(p)
            m = members.get(identity)
            if m is None or p.eligible != m.eligible or p.exclusion_reasons != m.reasons:
                raise ValueError("evaluation_prediction_membership_mismatch")
            bit = 1 << names.index(p.model)
            if masks[identity] & bit:
                raise ValueError("evaluation_duplicate_prediction")
            masks[identity] |= bit
            units = (
                p.predicted_units
                if isinstance(p, ModelPrediction)
                else p.estimate.predicted_units
                if p.estimate
                else None
            )
            choice = choices[p.fold]
            aliases: list[str] = [p.model]
            if p.model == choice.selected:
                aliases.append("validation_selected")
            if p.model == choice.baseline:
                aliases.append("validation_baseline")
            if m.eligible:
                actual = labels[identity].observed_sales_units
                if actual is None:
                    raise ValueError("evaluation_eligible_label_missing")
                for method in aliases:
                    accumulators[p.fold, p.role, method].add(actual, units)
    if set(masks) != set(members) or any(v != (1 << len(names)) - 1 for v in masks.values()):
        raise ValueError("evaluation_prediction_method_coverage_mismatch")
    saved = [
        SegmentMetric.model_validate_json(json.dumps(m))
        for m in json.loads(read_bytes(root / "quality", "segments.json"))["rows"]
        if m["fold"] == "pooled" and m["dimension"] == "global"
    ]
    result = []
    for segment in sorted(saved, key=lambda m: (m.role, m.method)):
        # Reuse the frozen evaluator and fold pooling, including explicit zero denominators.
        point = pool_metrics(
            [accumulators[f, segment.role, segment.method].result() for f in sorted(choices)]
        )
        actual_point = point.model_dump(mode="json")
        stored_point = segment.point.model_dump(mode="json")
        if any(
            (
                actual_point[k] != stored_point[k]
                if not isinstance(v, float)
                else stored_point[k] is None
                or not math.isclose(v, stored_point[k], rel_tol=1e-12, abs_tol=1e-9)
            )
            for k, v in actual_point.items()
        ):
            raise ValueError("evaluation_recorded_point_metric_replay_mismatch")
        rows = [m for m in members.values() if m.role == segment.role]
        if segment.total_rows != len(rows) or segment.excluded_rows != sum(
            not m.eligible for m in rows
        ):
            raise ValueError("evaluation_recorded_membership_count_mismatch")
        result.append(
            EvaluationMetric.model_validate_json(
                json.dumps(
                    dict(
                        role=segment.role,
                        method=segment.method,
                        total_rows=segment.total_rows,
                        excluded_rows=segment.excluded_rows,
                        point=stored_point,
                    )
                )
            )
        )
    scope = EvaluationScope.model_validate_json(
        json.dumps(
            dict(
                product_ids=sorted({m.product_id for m in members.values()}),
                selling_location_ids=sorted({m.selling_location_id for m in members.values()}),
                channels=sorted({m.channel for m in members.values()}),
            )
        )
    )
    return scope, len(members), split.tables["memberships"].content_sha256, tuple(result)
