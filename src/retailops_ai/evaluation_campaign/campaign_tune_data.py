"""Streaming paired Tune labels/predictions; fixed accumulators, no label sampling."""

import hashlib
import math
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path
from typing import Any, cast

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_fit_contract import Family
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    CampaignForecastRawPrediction,
    CampaignForecastScoreReceipt,
)
from retailops_ai.evaluation_campaign.campaign_score_metrics import RawMetrics
from retailops_ai.evaluation_campaign.campaign_tune_contract import (
    Baseline,
    CampaignForecastChoice,
    CampaignForecastTunePlan,
    CampaignForecastTuneSelection,
    Model,
)
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastExample,
    PhysicalForecastManifest,
)
from retailops_ai.forecasting.functional_v12_quality import StableSum
from retailops_ai.forecasting.quality_v2 import interval_score
from retailops_ai.forecasting.quality_v2_contract import CentralInterval
from retailops_ai.source_snapshot.files import SnapshotError, regular_file


@dataclass
class BandMetrics:
    rows: int = 0
    covered: int = 0
    score: StableSum = field(default_factory=StableSum)
    width: StableSum = field(default_factory=StableSum)

    def add(self, actual: int, band: CentralInterval | None, nominal: float) -> None:
        if band is None:
            return
        score = interval_score(actual, band, nominal)
        if not math.isfinite(score):
            raise SnapshotError("campaign_tune_nonfinite_interval_score")
        self.rows += 1
        self.covered += band.lower <= actual <= band.upper
        self.score.add(score)
        self.width.add(band.upper - band.lower)

    def result(self, eligible: int) -> dict[str, Any]:
        complete = eligible > 0 and self.rows == eligible
        return {
            "predicted_rows": self.rows,
            "complete": complete,
            "mean_score": self.score.value() / eligible if complete else None,
            "mean_width": self.width.value() / eligible if complete else None,
            "coverage": self.covered / eligible if complete else None,
        }


def paired_metrics(
    dataset: Path,
    bundle: Path,
    manifest: PhysicalForecastManifest,
    receipt: CampaignForecastScoreReceipt,
    plan: CampaignForecastTunePlan,
) -> dict[str, Any]:
    points = RawMetrics()
    bands = {(h, m): BandMetrics() for h in ("all", *map(str, range(1, 15))) for m in MODELS}
    keys, eligible_keys, examples, predictions, baselines = (hashlib.sha256() for _ in range(5))
    size = prediction_size = rows = eligible = 0
    previous = None
    with (
        regular_file(dataset, "tune.jsonl") as labels,
        regular_file(bundle, "predictions.jsonl") as forecasts,
    ):
        lines = iter(lambda: labels.readline(manifest.descriptor.recipe.max_record_bytes + 1), b"")
        predicted_lines = iter(lambda: forecasts.readline(65537), b"")
        for raw, predicted in zip_longest(lines, predicted_lines):
            if raw is None or predicted is None:
                raise SnapshotError("campaign_tune_complete_pair_count_mismatch")
            if (
                len(raw) > manifest.descriptor.recipe.max_record_bytes
                or len(predicted) > 65536
                or not raw.endswith(b"\n")
                or not predicted.endswith(b"\n")
            ):
                raise SnapshotError("campaign_tune_record_limit")
            example = PhysicalForecastExample.model_validate_json(raw)
            row = CampaignForecastRawPrediction.model_validate_json(predicted)
            key = membership_key(example.membership)
            outcome = example.outcome
            if (
                example.membership.role != "tune"
                or row.role != "tune"
                or outcome is None
                or (previous is not None and key <= previous)
                or membership_key(row) != key
                or row.example_sha256 != hashlib.sha256(raw[:-1]).hexdigest()
                or row.eligible != outcome.eligible
                or row.exclusion_reasons != outcome.reasons
                or raw != canonical_bytes(example.model_dump(mode="json")) + b"\n"
                or predicted != canonical_bytes(row.model_dump(mode="json")) + b"\n"
            ):
                raise SnapshotError("campaign_tune_pair_key_hash_or_eligibility_mismatch")
            previous = key
            rows += 1
            if rows > plan.max_rows:
                raise SnapshotError("campaign_tune_population_budget")
            size += len(raw)
            prediction_size += len(predicted)
            examples.update(raw)
            predictions.update(predicted)
            keys.update(key + b"\n")
            baselines.update(
                canonical_bytes(
                    {
                        "key": key.decode(),
                        "values": [v.model_dump(mode="json") for v in row.values[:3]],
                    }
                )
                + b"\n"
            )
            actual = outcome.label.observed_sales_units
            if row.eligible:
                if actual is None:
                    raise SnapshotError("campaign_tune_eligible_actual_missing")
                eligible += 1
                eligible_keys.update(key + b"\n")
                for model, forecast in zip(MODELS, row.values, strict=True):
                    for value in (forecast.mean, forecast.median):
                        if value is not None:
                            error = value - actual
                            if not math.isfinite(error * error):
                                raise SnapshotError("campaign_tune_nonfinite_point_error")
                    for horizon in ("all", str(row.horizon_days)):
                        bands[horizon, model].add(
                            actual, forecast.interval, plan.policy.nominal_coverage
                        )
            points.add(row, actual)
    expected = manifest.descriptor.populations["tune"]
    if (
        (rows, eligible, size, examples.hexdigest(), keys.hexdigest())
        != (
            expected.row_count,
            expected.eligible_rows,
            expected.size_bytes,
            expected.sha256,
            expected.keys_sha256,
        )
        or (rows, eligible, examples.hexdigest(), keys.hexdigest(), eligible_keys.hexdigest())
        != (
            receipt.rows,
            receipt.eligible_rows,
            receipt.role_population_sha256,
            receipt.keys_sha256,
            receipt.eligible_keys_sha256,
        )
        or predictions.hexdigest() != receipt.artifact_files["predictions.jsonl"]
        or prediction_size > receipt.artifact_bytes
        or rows == 0
    ):
        raise SnapshotError("campaign_tune_complete_population_or_prediction_digest_mismatch")
    metrics = points.result()
    metrics["scope"] = "tune_only_complete_trial_diagnostics_not_independent_qualification"
    for segment in metrics["segments"]:
        for model in MODELS:
            segment["models"][model]["interval"] = bands[segment["horizon"], model].result(
                segment["eligible_rows"]
            )
    return {
        "score_operation_id": receipt.operation_id,
        "metrics": metrics,
        "rows": rows,
        "eligible_rows": eligible,
        "keys_sha256": keys.hexdigest(),
        "eligible_keys_sha256": eligible_keys.hexdigest(),
        "role_population_sha256": examples.hexdigest(),
        "baseline_predictions_sha256": baselines.hexdigest(),
    }


def choose(
    trials: list[dict[str, Any]],
    scores: dict[str, CampaignForecastScoreReceipt],
    plan: CampaignForecastTunePlan,
) -> CampaignForecastTuneSelection:
    if [t["score_operation_id"] for t in trials] != list(plan.score_operation_ids):
        raise SnapshotError("campaign_tune_trial_order_mismatch")
    first = trials[0]
    population = (
        "rows",
        "eligible_rows",
        "keys_sha256",
        "eligible_keys_sha256",
        "role_population_sha256",
        "baseline_predictions_sha256",
    )
    if any(any(t[k] != first[k] for k in population) for t in trials):
        raise SnapshotError("campaign_tune_trial_population_or_baselines_differ")
    rows, n = first["rows"], first["eligible_rows"]
    reasons = []
    if n < plan.policy.minimum_rows:
        reasons.append("insufficient_tune_rows")
    if n / rows < plan.policy.minimum_eligibility_coverage:
        reasons.append("eligibility_coverage_below_minimum")
    globals_ = []
    for trial in trials:
        segments = trial["metrics"]["segments"]
        if (
            len(segments) != 15
            or [s["horizon"] for s in segments] != ["all", *map(str, range(1, 15))]
            or (segments[0]["rows"], segments[0]["eligible_rows"]) != (rows, n)
            or sum(s["rows"] for s in segments[1:]) != rows
            or sum(s["eligible_rows"] for s in segments[1:]) != n
        ):
            raise SnapshotError("campaign_tune_metric_population_mismatch")
        globals_.append(segments[0]["models"])

    def best_baseline(head: str, metric: str) -> str | None:
        values = [
            (globals_[0][m][head][metric], i, m)
            for i, m in enumerate(MODELS[:3])
            if globals_[0][m][head]["complete"] and globals_[0][m][head][metric] is not None
        ]
        return min(values)[2] if values else None

    baseline_mean = best_baseline("mean", "mse")
    baseline_median = best_baseline("median", "mae")
    baseline_interval = best_baseline("interval", "mean_score")
    for head, reference in (
        ("mean", baseline_mean),
        ("median", baseline_median),
        ("interval", baseline_interval),
    ):
        if reference is None:
            reasons.append("no_complete_baseline_" + head)

    def head_choice(head: str, reference: str | None) -> CampaignForecastChoice | None:
        if reference is None or reasons:
            return None
        metric = "mse" if head == "mean" else "mae"
        options = []
        for index, global_ in enumerate(globals_):
            for model_index, model in enumerate(MODELS[3:], 3):
                value = global_[model][head]
                if not value["complete"] or value[metric] is None:
                    continue
                if (
                    head == "mean"
                    and value["normalized_bias"] is not None
                    and abs(value["normalized_bias"])
                    > plan.policy.maximum_absolute_normalized_mean_bias
                ):
                    continue
                options.append((value[metric], model_index, index, model))
        choice = CampaignForecastChoice(model=cast(Model, reference))
        if not options:
            return choice
        value, _, index, model = min(options)
        reference_value = globals_[0][reference][head][metric]
        threshold = (
            reference_value * (1 - plan.policy.minimum_relative_median_improvement)
            if head == "median"
            else reference_value
        )
        if value >= threshold:
            return choice
        receipt = scores[plan.score_operation_ids[index]]
        family = cast(Family, "rf" if model == "rf_mean" else model)
        return CampaignForecastChoice(
            model=cast(Model, model),
            score_operation_id=receipt.operation_id,
            fit_operation_id=receipt.plan.fit_operation_ids[family],
            model_artifact_sha256=receipt.model_artifact_sha256[family],
        )

    mean = head_choice("mean", baseline_mean)
    median = head_choice("median", baseline_median)
    return CampaignForecastTuneSelection(
        status="not_ready" if reasons else "selected_for_independent_evaluation",
        rows=rows,
        eligible_rows=n,
        baseline_mean=cast(Baseline | None, baseline_mean),
        baseline_median=cast(Baseline | None, baseline_median),
        baseline_interval=cast(Baseline | None, baseline_interval),
        mean=mean,
        median=median,
        candidate_interval_center=median,
        reasons=tuple(reasons),
    )
