"""One streamed Calibration label pass; exact bounded-disk residual order statistics."""

import hashlib
import math
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from itertools import zip_longest
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibration,
    CampaignForecastCalibrationPlan,
    CampaignForecastHorizonCalibration,
    residual_rank,
)
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    CampaignForecastRawPrediction,
    CampaignForecastScoreReceipt,
)
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneSelection
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastExample,
    PhysicalForecastManifest,
)
from retailops_ai.evaluation_campaign.physical_forecast import _index
from retailops_ai.forecasting.quality_v2_contract import CentralInterval
from retailops_ai.source_snapshot.files import SnapshotError, regular_file


def fit_horizons(
    db: sqlite3.Connection,
    counts: dict[int, tuple[int, int]],
    selection: CampaignForecastTuneSelection,
    score_operation_id: str,
    plan: CampaignForecastCalibrationPlan,
) -> CampaignForecastCalibration:
    if selection.status != "selected_for_independent_evaluation" or selection.median is None:
        raise SnapshotError("campaign_calibration_requires_completed_tune_choice")
    horizons = []
    for horizon in range(1, 15):
        rows, n = counts[horizon]
        rank = residual_rank(n)
        reasons = []
        if n < plan.minimum_eligible_rows_per_horizon:
            reasons.append("insufficient_calibration_rows")
        if rows == 0 or n / rows < plan.minimum_eligibility_coverage_per_horizon:
            reasons.append("calibration_eligibility_coverage_below_minimum")
        if rank > n:
            reasons.append("finite_sample_order_statistic_unavailable")
        observed = db.execute(
            "SELECT COUNT(*) FROM residuals WHERE horizon=?", (horizon,)
        ).fetchone()[0]
        if observed != n:
            raise SnapshotError("campaign_calibration_residual_count_mismatch")
        radius = None
        if not reasons:
            radius = db.execute(
                "SELECT error FROM residuals WHERE horizon=? ORDER BY error,ordinal LIMIT 1 OFFSET ?",
                (horizon, rank - 1),
            ).fetchone()[0]
            if not math.isfinite(radius) or radius < 0:
                raise SnapshotError("campaign_calibration_nonfinite_radius")
        horizons.append(
            CampaignForecastHorizonCalibration(
                horizon_days=horizon,
                rows=rows,
                eligible_rows=n,
                quantile_rank=rank,
                radius=radius,
                reasons=tuple(reasons),
            )
        )
    fitted = all(not h.reasons for h in horizons)
    return CampaignForecastCalibration(
        status="fitted_for_independent_evaluation" if fitted else "not_ready",
        selection=selection,
        center=selection.median,
        calibration_score_operation_id=score_operation_id,
        rows=sum(r for r, _ in counts.values()),
        eligible_rows=sum(n for _, n in counts.values()),
        horizons=tuple(horizons),
        calibration_fitted=fitted,
    )


def pairs(
    dataset: Path,
    bundle: Path,
    manifest: PhysicalForecastManifest,
    score: CampaignForecastScoreReceipt,
    plan: CampaignForecastCalibrationPlan,
) -> Iterable[tuple[PhysicalForecastExample, CampaignForecastRawPrediction]]:
    """Yield exact calibration pairs; full validation completes when exhausted."""
    keys, eligible_keys, labels, predictions = (hashlib.sha256() for _ in range(4))
    rows = eligible = size = predicted_size = 0
    previous = None
    with (
        regular_file(dataset, "calibration.jsonl") as outcomes,
        regular_file(bundle, "predictions.jsonl") as forecasts,
    ):
        actual_lines = iter(
            lambda: outcomes.readline(manifest.descriptor.recipe.max_record_bytes + 1), b""
        )
        predicted_lines = iter(lambda: forecasts.readline(65537), b"")
        for raw, predicted in zip_longest(actual_lines, predicted_lines):
            if raw is None or predicted is None:
                raise SnapshotError("campaign_calibration_pair_count_mismatch")
            if len(raw) > manifest.descriptor.recipe.max_record_bytes or len(predicted) > 65536:
                raise SnapshotError("campaign_calibration_record_budget")
            example = PhysicalForecastExample.model_validate_json(raw)
            forecast = CampaignForecastRawPrediction.model_validate_json(predicted)
            key = membership_key(example.membership)
            outcome = example.outcome
            if (
                example.membership.role != "calibration"
                or forecast.role != "calibration"
                or outcome is None
                or (previous is not None and key <= previous)
                or membership_key(forecast) != key
                or forecast.example_sha256 != hashlib.sha256(raw[:-1]).hexdigest()
                or forecast.eligible != outcome.eligible
                or forecast.exclusion_reasons != outcome.reasons
                or raw != canonical_bytes(example.model_dump(mode="json")) + b"\n"
                or predicted != canonical_bytes(forecast.model_dump(mode="json")) + b"\n"
            ):
                raise SnapshotError("campaign_calibration_pair_binding_mismatch")
            previous = key
            rows += 1
            if rows > plan.max_rows:
                raise SnapshotError("campaign_calibration_population_budget")
            keys.update(key + b"\n")
            labels.update(raw)
            predictions.update(predicted)
            size += len(raw)
            predicted_size += len(predicted)
            if forecast.eligible:
                eligible += 1
                eligible_keys.update(key + b"\n")
            yield example, forecast
    expected = manifest.descriptor.populations["calibration"]
    if (
        (rows, eligible, size, labels.hexdigest(), keys.hexdigest())
        != (
            expected.row_count,
            expected.eligible_rows,
            expected.size_bytes,
            expected.sha256,
            expected.keys_sha256,
        )
        or (rows, eligible, labels.hexdigest(), keys.hexdigest(), eligible_keys.hexdigest())
        != (
            score.rows,
            score.eligible_rows,
            score.role_population_sha256,
            score.keys_sha256,
            score.eligible_keys_sha256,
        )
        or predictions.hexdigest() != score.artifact_files["predictions.jsonl"]
        or predicted_size > score.artifact_bytes
        or rows == 0
    ):
        raise SnapshotError(
            "campaign_calibration_complete_population_or_prediction_digest_mismatch"
        )


def fit(
    root: Path,
    dataset: Path,
    bundle: Path,
    manifest: PhysicalForecastManifest,
    score: CampaignForecastScoreReceipt,
    selection: CampaignForecastTuneSelection,
    plan: CampaignForecastCalibrationPlan,
) -> tuple[CampaignForecastCalibration, dict[str, Any]]:
    if selection.median is None or selection.median.model == "rf_mean":
        raise SnapshotError("campaign_calibration_median_center_required")
    counts = {h: (0, 0) for h in range(1, 15)}
    column = MODELS.index(selection.median.model)
    digest = hashlib.sha256()
    with closing(_index(root / "residuals.sqlite", plan.max_index_bytes)) as db:
        db.execute(
            "CREATE TABLE residuals(horizon INTEGER NOT NULL,error REAL NOT NULL,ordinal INTEGER NOT NULL)"
        )
        db.execute("CREATE INDEX ordered_residuals ON residuals(horizon,error,ordinal)")
        for ordinal, (example, prediction) in enumerate(
            pairs(dataset, bundle, manifest, score, plan)
        ):
            horizon = prediction.horizon_days
            rows, eligible = counts[horizon]
            counts[horizon] = rows + 1, eligible + int(prediction.eligible)
            if not prediction.eligible:
                continue
            actual = (
                example.outcome.label.observed_sales_units if example.outcome is not None else None
            )
            median = prediction.values[column].median
            if actual is None or median is None:
                raise SnapshotError("campaign_calibration_selected_median_or_actual_missing")
            residual = abs(median - actual)
            if not math.isfinite(residual):
                raise SnapshotError("campaign_calibration_nonfinite_residual")
            db.execute("INSERT INTO residuals VALUES (?,?,?)", (horizon, residual, ordinal))
            digest.update(
                canonical_bytes({"key": membership_key(prediction).decode(), "residual": residual})
                + b"\n"
            )
        db.commit()
        calibrated = fit_horizons(db, counts, selection, score.operation_id, plan)
    return calibrated, {
        **{
            key: getattr(score, key)
            for key in (
                "rows",
                "eligible_rows",
                "keys_sha256",
                "eligible_keys_sha256",
                "role_population_sha256",
            )
        },
        "selected_median_residuals_sha256": digest.hexdigest(),
        "selected_prediction_file_sha256": score.artifact_files["predictions.jsonl"],
        "full_calibration_label_passes": 1,
        "tune_label_passes": 0,
        "independent_or_final_label_passes": 0,
        "architecture_reselected": False,
    }


def interval(
    calibration: CampaignForecastCalibration, horizon: int, median: float
) -> CentralInterval:
    """Apply fixed radii without fitting or reading labels; temporal coverage is empirical."""
    if calibration.status != "fitted_for_independent_evaluation" or not 1 <= horizon <= 14:
        raise SnapshotError("campaign_calibration_unavailable_or_horizon_invalid")
    radius = calibration.horizons[horizon - 1].radius
    if (
        radius is None
        or not math.isfinite(median)
        or median < 0
        or not math.isfinite(median + radius)
    ):
        raise SnapshotError("campaign_calibration_nonfinite_or_negative_interval_input")
    return CentralInterval(lower=max(0.0, median - radius), upper=median + radius)
