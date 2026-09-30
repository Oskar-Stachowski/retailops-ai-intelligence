"""Compare preregistered mean variants on the retained train/validation cache only."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.functional_campaign import load_campaign
from retailops_ai.forecasting.functional_contract import FunctionalPipeline
from retailops_ai.forecasting.functional_development import (
    iter_development_rows,
    load_development_cache,
)
from retailops_ai.forecasting.functional_models import functional_code
from retailops_ai.forecasting.functional_recipe import Observation
from retailops_ai.forecasting.functional_v12_quality import (
    CampaignObservation,
    StreamingCampaignScorer,
)
from retailops_ai.forecasting.functional_v12_recipe import (
    FunctionalV12Policy,
    PreparedV12Predictor,
    fit_recipe_v12,
)
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.forecasting.model_trees import TreePredictor
from retailops_ai.forecasting.preprocessing import FittedState
from retailops_ai.forecasting.quality_v2_contract import ProtocolObservation
from retailops_ai.source_snapshot.files import SnapshotError


def variant_policies(with_hgb: bool = False) -> dict[str, FunctionalV12Policy]:
    result = {"baseline": FunctionalV12Policy(mean_variant="baseline")}
    for zero in ("validation_only", "train_validation_pooled"):
        result[f"zero-only-{zero}"] = FunctionalV12Policy.model_validate_json(
            json.dumps({"mean_variant": "zero_only", "zero_estimation": zero})
        )
    variants = [("additive", "additive", 0.5)]
    if with_hgb:
        variants.extend((f"hgb-{weight:g}", "hgb_blend", weight) for weight in (0.25, 0.5, 1.0))
    for name, variant, weight in variants:
        for alpha in (0.0, 50.0, 200.0):
            for zero in ("validation_only", "train_validation_pooled"):
                result[f"{name}-alpha{alpha:g}-{zero}"] = FunctionalV12Policy.model_validate_json(
                    json.dumps(
                        {
                            "mean_variant": variant,
                            "prior_strength": alpha,
                            "hgb_weight": weight,
                            "zero_estimation": zero,
                        }
                    )
                )
    return result


def observation(record: dict[str, Any], hgb_mean: float | None = None) -> Observation:
    points = dict(record["baseline_points"])
    if hgb_mean is not None:
        points["hgb_mean"] = hgb_mean
    return Observation(
        key=record["key"],
        fold=record["fold"],
        role=record["role"],
        origin=record["origin"],
        volume=record["volume"],
        category=record["category"],
        channel=record["channel"],
        horizon=record["horizon"],
        reasons=tuple(record["reasons"]),
        actual=record["actual"],
        available=record["label_available_at"],
        points=points,
        bands={k: tuple(v) if v is not None else None for k, v in record["baseline_bands"].items()},
    )


def code_receipt() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    names = (
        "scripts/check_forecast_functional_v12_development.py",
        "src/retailops_ai/forecasting/functional_v12_recipe.py",
        "src/retailops_ai/forecasting/functional_v12_quality.py",
        "src/retailops_ai/forecasting/functional_development.py",
    )
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def predict_retained_mean(
    records: list[dict[str, Any]],
    *,
    fold_name: str,
    run: Path,
    cache: Path,
    manifest: dict[str, Any],
) -> tuple[list[float], dict[str, Any]]:
    path = run / "campaign" / "models" / fold_name / "hgb_mean.json"
    model = FunctionalPipeline.model_validate_json(path.read_bytes())
    state_name = manifest["descriptor"]["folds"][fold_name]["preprocessing"]
    state = FittedState.model_validate_json((cache / state_name).read_bytes())
    if (
        model.head != "hgb_mean"
        or model.preprocessing.descriptor != state.descriptor
        or model.code_sha256 != canonical_sha256(functional_code())
        or model.preprocessing.descriptor.feature_set_id != manifest["descriptor"]["feature_set_id"]
        or model.preprocessing.descriptor.split_id != manifest["descriptor"]["split_id"]
    ):
        raise SnapshotError("functional_v12_development_retained_model_binding")
    predictor = TreePredictor(model.estimator)
    result: list[float] = []
    for start in range(0, len(records), 4096):
        batch = np.asarray([r["vector"] for r in records[start : start + 4096]], dtype=np.float64)
        result.extend(float(v) for v in predictor.matrix(batch))
    return result, {
        "model_id": model.model_id,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "preprocessing_id": model.preprocessing.preprocessing_id,
        "model_fits": 0,
        "prediction_batch_limit": 4096,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.report.exists():
        raise SnapshotError("functional_v12_development_report_already_exists")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    pins = code_receipt()
    manifest = load_development_cache(args.cache, args.features, args.split)
    desc = manifest["descriptor"]
    if args.with_hgb:
        if args.run is None:
            raise SnapshotError("functional_v12_development_hgb_requires_retained_run")
        campaign = load_campaign(args.run / "campaign")
        if (
            campaign["descriptor"]["feature_set_id"] != desc["feature_set_id"]
            or campaign["descriptor"]["split_id"] != desc["split_id"]
        ):
            raise SnapshotError("functional_v12_development_campaign_parent_binding")
    variants = variant_policies(args.with_hgb)
    folds = list(desc["folds"])
    report: dict[str, Any] = {
        "started_at": started,
        "status": "running",
        "model_status": "not_ready",
        "evaluation_use": "calibration_diagnostic_not_independent_qualification",
        "cache_id": manifest["cache_id"],
        "code": pins,
        "v11_code_pins": functional_code(),
        "holdout_rows_read": 0,
        "source_generated": False,
        "model_fits": 0,
        "policies": {name: policy.model_dump(mode="json") for name, policy in variants.items()},
        "folds": {},
    }

    def save() -> None:
        args.report.write_bytes(canonical_bytes(report) + b"\n")

    with args.report.open("xb") as stream:
        stream.write(canonical_bytes(report) + b"\n")
    try:
        with ExitStack() as resources:
            scorers: dict[str, StreamingCampaignScorer] = {}
            for fold_name in folds:
                meta = desc["folds"][fold_name]
                fold = FoldPlan.model_validate_json(canonical_bytes(meta["plan"]))
                raw = list(iter_development_rows(args.cache, manifest, fold_name, "validation"))
                means: list[float | None] = [None] * len(raw)
                model_receipt = None
                if args.with_hgb:
                    prediction, model_receipt = predict_retained_mean(
                        raw, fold_name=fold_name, run=args.run, cache=args.cache, manifest=manifest
                    )
                    means = list(prediction)
                validation = [observation(r, p) for r, p in zip(raw, means, strict=True)]
                del raw, means
                training = [
                    observation(r)
                    for r in iter_development_rows(
                        args.cache, manifest, fold_name, "train", eligible_only=True
                    )
                ]
                eligible = [row for row in validation if not row.reasons]
                if not scorers:
                    dimensions = {
                        "category": sorted({r.category for r in validation}),
                        "channel": sorted({r.channel for r in validation}),
                        "volume": sorted({r.volume for r in validation}),
                    }
                    scorers = {
                        name: resources.enter_context(
                            StreamingCampaignScorer(
                                cohorts=["v11-development"], folds=folds, dimensions=dimensions
                            )
                        )
                        for name in variants
                    }
                fold_report: dict[str, Any] = {
                    "validation_rows": len(validation),
                    "eligible_validation_rows": len(eligible),
                    "eligible_train_rows": len(training),
                    "retained_mean_model": model_receipt,
                    "recipes": {},
                }
                report["folds"][fold_name] = fold_report
                for name, policy in variants.items():
                    recipe = fit_recipe_v12(eligible, fold, policy, training_rows=training)
                    fold_report["recipes"][name] = recipe
                    predictor = PreparedV12Predictor(recipe)
                    calibration = recipe["reference_recipe"]["calibration"]
                    for row in validation:
                        candidate, baseline, metadata = predictor.predict(row)
                        cell = calibration.get(metadata["baseline"])
                        n = cell["rows"] if cell else None
                        scorers[name].add(
                            CampaignObservation(
                                cohort_id="v11-development",
                                fold=fold_name,
                                role="validation",
                                horizon=row.horizon,
                                category=row.category,
                                channel=row.channel,
                                volume=row.volume,
                                observation=ProtocolObservation(
                                    key=row.key,
                                    actual=row.actual,
                                    exclusion_reasons=row.reasons,
                                    candidate=candidate,
                                    baseline=baseline,
                                ),
                                retained_median_baseline=True,
                                candidate_calibration_rows=n,
                                baseline_calibration_rows=n,
                            )
                        )
                    print(
                        json.dumps(
                            {"fold": fold_name, "variant": name, "stage": "scored_validation"}
                        ),
                        flush=True,
                    )
                save()
            results: dict[str, dict[str, Any]] = {}
            for name, scorer in scorers.items():
                result = scorer.finalize()
                segments = [s for s in result["segments"] if s["role"] == "validation"]
                results[name] = {
                    "model_status": "not_ready",
                    "segment_counts": dict(Counter(s["status"] for s in segments)),
                    "failed_reasons": dict(
                        Counter(reason for s in segments for reason in s["failed_reasons"])
                    ),
                    "segments": segments,
                }
            if code_receipt() != pins:
                raise SnapshotError("functional_v12_development_code_changed_during_experiment")
            report.update(
                status="completed", finished_at=datetime.now(UTC).isoformat(), variants=results
            )
            report["development_ranking_not_model_selection"] = sorted(
                results,
                key=lambda name: (
                    results[name]["segment_counts"].get("failed", 0)
                    + results[name]["segment_counts"].get("not_ready", 0),
                    next(
                        s["candidate"]["mean"]["mse"]
                        for s in results[name]["segments"]
                        if s["fold"] == "pooled" and s["dimension"] == "global"
                    ),
                    name,
                ),
            )
            save()
    except Exception as exc:
        report.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        save()
        raise
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cache", "features", "split", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--with-hgb", action="store_true")
    args = parser.parse_args()
    result = run(args)
    print(
        json.dumps(
            {"status": result["status"], "report": str(args.report), "model_status": "not_ready"}
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
