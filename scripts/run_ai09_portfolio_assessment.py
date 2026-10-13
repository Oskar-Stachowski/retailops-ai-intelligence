"""Explicit calibration of the approved frozen portfolio; no final source generation."""

from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from itertools import islice
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import measure_ai09_development_capacity as probe  # noqa: E402
import run_ai09_portfolio_repair as repair  # noqa: E402
from ai09_preparation_worker import digest, runtime  # noqa: E402

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256  # noqa: E402

PLAN = ROOT / "docs/reference/ai09-portfolio-calibration-v1.json"
ASSESSMENT = ROOT / "docs/reference/ai09-portfolio-assessment-v1.json"


def prepare(output: Path) -> None:
    from retailops_ai.evaluation_campaign.development_storage import shared_records
    from retailops_ai.evaluation_campaign.portfolio_data_io import write_population
    from retailops_ai.evaluation_campaign.portfolio_model_io import seal_models
    from retailops_ai.forecasting.functional_campaign import index_parents, samples
    from retailops_ai.forecasting.functional_contract import FunctionalPolicy

    original = output / "download/repair"
    completed = probe.read(original / "result.json")
    if completed["status"] != "completed_development_diagnostic":
        raise ValueError("portfolio_calibration_requires_completed_comparison")
    parents = repair.original_parents(original / "original", repair.frozen_plan())
    models = seal_models(original / "comparison", output / "models")
    with sqlite3.connect(output / "calibration-index.sqlite") as db:
        feature, split, _ = index_parents(
            db, parents["features"], parents["split"], FunctionalPolicy()
        )
        fold = split.descriptor.resolved_policy.folds[0]
        if fold.name != "portfolio-development":
            raise ValueError("portfolio_calibration_wrong_fold")

        def records() -> Any:
            for row, member, label, history in shared_records(
                db, samples(db, fold, "development_holdout")
            ):
                if (
                    member.role != "development_holdout"
                    or label.role != member.role
                    or fold.role(row.forecast_origin.date()) != member.role
                ):
                    raise ValueError("portfolio_calibration_wrong_original_role")
                yield row, history, label.observed_sales_units, member.reasons

        population = write_population(output, records(), role="calibration")
    (output / "calibration-index.sqlite").unlink()
    probe.write(
        output / "prepared.json",
        {
            "models": models,
            "population": population,
            "feature_set_id": feature.feature_set_id,
            "split_id": split.split_id,
            "original_role": "development_holdout",
            "assessment_role": "calibration",
            "independent_quality_evaluation": False,
            "source_regenerated": False,
            "models_refitted": False,
        },
    )


def infer(output: Path, models: Path) -> None:
    from retailops_ai.evaluation_campaign.portfolio_assessment import predict
    from retailops_ai.evaluation_campaign.portfolio_data_io import inference_windows
    from retailops_ai.evaluation_campaign.portfolio_model_io import load_models, verify_models
    from retailops_ai.worker_resources import worker_peak_rss_bytes

    started, cpu = time.monotonic(), time.process_time()
    tensorflow, trees, resources = load_models(models)
    loaded = time.monotonic()
    windows = iter(inference_windows(output))
    count = 0
    with (output / "predictions.jsonl").open("xb") as stream:
        while batch := list(islice(windows, 64)):
            predictions = predict(
                batch, tensorflow=tensorflow.model, state=tensorflow.state, trees=trees
            )
            for key, values in predictions.items():
                stream.write(
                    canonical_bytes(
                        {
                            "key": key.decode(),
                            "predictions": {
                                name: value.model_dump(mode="json")
                                for name, value in values.items()
                            },
                        }
                    )
                    + b"\n"
                )
                count += 1
    verify_models(models)
    probe.write(
        output / "inference-resources.json",
        resources
        | {
            "prediction_seconds": time.monotonic() - loaded,
            "wall_seconds": time.monotonic() - started,
            "cpu_seconds": time.process_time() - cpu,
            "peak_self_rss_bytes": worker_peak_rss_bytes(),
            "predicted_rows_per_model": count,
            "fresh_cpu_process": True,
        },
    )


def calibrate(output: Path) -> None:
    from retailops_ai.evaluation_campaign.portfolio_assessment import fit_intervals
    from retailops_ai.evaluation_campaign.portfolio_data_io import scored_rows
    from retailops_ai.evaluation_campaign.portfolio_model_io import verify_models

    calibration = fit_intervals(scored_rows(output))
    probe.write(output / "calibration.json", calibration)
    if calibration["status"] != "fitted":
        raise ValueError("portfolio_calibration_not_ready")
    models = verify_models(output / "models")
    report = probe.read(output / "models/report.json")
    groups = {d: sorted(values) for d, values in report["models"]["history28"]["segments"].items()}
    assessment = probe.read(ASSESSMENT)
    probe.write(
        output / "freeze.json",
        {
            "version": "ai09-portfolio-final-freeze-1.0.0",
            "assessment": assessment,
            "assessment_sha256": canonical_sha256(assessment),
            "model_bundle_sha256": canonical_sha256(models),
            "calibration_sha256": canonical_sha256(calibration),
            "required_groups": groups,
            "candidate": "tensorflow",
            "reference": "history28",
            "comparators": ["history7", "weekday28", "rf_mean", "hgb"],
            "no_final_refit_or_reselection": True,
            "final_generation_not_yet_started": True,
        },
    )


def run(output: Path) -> None:
    from ai09_checkpoint_transport import validate_metadata
    from download_forecast_cohort_checkpoint import _download_zip, artifact_metadata
    from run_ai09_preparation import environment

    probe.require_remote()
    if os.environ.get("GITHUB_RUN_ATTEMPT") != "1":
        raise ValueError("portfolio_calibration_rerun_requires_explicit_plan")
    probe.clean_pin(ROOT, os.environ["GITHUB_SHA"])
    plan, assessment = probe.read(PLAN), probe.read(ASSESSMENT)
    if plan["assessment_sha256"] != canonical_sha256(assessment) or plan["maximum_attempts"] != 1:
        raise ValueError("portfolio_calibration_plan_changed")
    output.mkdir(mode=0o700)
    probe.write(
        output / "intent.json",
        {
            "plan": plan,
            "assessment": assessment,
            "commit": os.environ["GITHUB_SHA"],
            "runtime_sha256": digest(runtime(ROOT, producer=False)),
            "role": "calibration",
            "whole_original_source_already_exposed_as_development": True,
            "final_test_opened": False,
        },
    )
    result: dict[str, Any] = {
        "status": "running",
        "resources": {},
        "final_test_opened": False,
        "stage_ready": False,
    }
    started = time.perf_counter()
    try:
        original = plan["original"]
        token = os.environ.get("GITHUB_TOKEN")
        metadata = artifact_metadata(original["artifact_id"], token)
        checksum = validate_metadata(
            metadata,
            artifact_id=original["artifact_id"],
            run_id=original["run_id"],
            head=original["head"],
            name=original["name"],
            maximum=plan["transport_maximum_bytes"],
        )
        if checksum != original["zip_sha256"]:
            raise ValueError("portfolio_calibration_artifact_changed")
        probe.write(output / "artifact.json", metadata)
        archive = output / "repair.zip"
        _download_zip(
            original["artifact_id"], archive, checksum, plan["transport_maximum_bytes"], token
        )
        repair.restore_archive(
            archive, output / "download", plan, roots=frozenset({"repair", "repair-preflight"})
        )
        for operation in ("prepare", "infer", "calibrate"):
            probe.write(
                output / (operation + "-intent.json"),
                {"operation": operation, "maximum_attempts": 1},
            )
            measured = probe.monitor(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(Path(__file__).resolve()),
                    "--output",
                    str(output),
                    "--operation",
                    operation,
                ],
                cwd=ROOT,
                env=environment(output),
                log=output / (operation + ".log"),
                roots=(output,),
                budgets=plan["budgets"],
                deadline=started + plan["budgets"]["wall_seconds"],
            )
            result["resources"][operation] = measured
            if measured["status"] != "passed":
                raise ValueError("portfolio_calibration_" + operation + "_failed")
        result["freeze_sha256"] = hashlib.sha256((output / "freeze.json").read_bytes()).hexdigest()
        result["status"] = "calibrated_and_frozen_before_final"
    except Exception as exc:
        result.update(status="failed", error_code=str(exc))
        raise
    finally:
        result["wall_seconds_including_transport"] = time.perf_counter() - started
        probe.write(output / "result.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--operation", choices=("prepare", "infer", "calibrate"))
    args = parser.parse_args()
    if args.operation is None:
        run(args.output)
    else:
        intent = probe.read(args.output / "intent.json")
        probe.clean_pin(ROOT, intent["commit"])
        if digest(runtime(ROOT, producer=False)) != intent["runtime_sha256"]:
            raise ValueError("portfolio_assessment_runtime_changed")
        if args.operation == "prepare":
            prepare(args.output)
        elif args.operation == "infer":
            infer(args.output, args.output / "models")
        else:
            calibrate(args.output)
