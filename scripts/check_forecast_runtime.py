"""AI05.5a contract gate; optional archived, unqualified model adapter acceptance without publication."""

import argparse
import hashlib
import json
import resource
import sys
import time
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.inputs import verify_inputs_package
from retailops_ai.forecasting.model_contract import ModelPipeline
from retailops_ai.forecasting.model_trees import ForecastAdapter
from retailops_ai.forecasting.models import decode_model_json
from retailops_ai.forecasting.run import verify_run
from retailops_ai.model_lifecycle.baseline import BaselineInput, BaselinePipeline, predict_examples
from retailops_ai.source_snapshot.files import regular_file
from retailops_ai.source_snapshot.protocol import resource_bytes

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--inputs-dir", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/forecast-runtime.json")
    args = parser.parse_args()
    if (args.run_dir is None) != (args.inputs_dir is None):
        parser.error("archived acceptance requires both --run-dir and --inputs-dir")
    report: dict[str, Any] = {
        "status": "passed",
        "scope": "AI05.5a runtime mechanics",
        "qualified_release_loaded": False,
        "forecast_quality_approved": False,
        "published_forecast_outputs": 0,
        "mlflow_mutations": 0,
        "aws_calls": 0,
    }
    if args.run_dir is not None:
        started = time.monotonic()
        archive = verify_run(args.run_dir)
        inputs = verify_inputs_package(args.inputs_dir)
        if archive.descriptor.feature_set_id != inputs.feature_manifest.feature_set_id:
            raise ValueError("runtime_acceptance_archived_features_mismatch")
        runtime_lock = hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest()
        input_lock = inputs.feature_manifest.descriptor.code.dependency_lock_sha256
        blockers = ["no_approved_qualified_release", "atomic_forecast_publication_not_implemented"]
        if input_lock != runtime_lock:
            blockers.append("archived_input_dependency_lock_mismatch")
        report.update(
            run_id=archive.run_id,
            profile_id=inputs.profile_id,
            source_quality_status=archive.descriptor.quality_status,
            source_gate_counts=archive.descriptor.gate_counts,
            source_dataset_id=inputs.feature_manifest.descriptor.parent.source_dataset_id,
            curated_dataset_id=inputs.feature_manifest.descriptor.parent.curated_dataset_id,
            feature_set_id=inputs.feature_manifest.feature_set_id,
            as_of=inputs.as_of_time.isoformat(),
            rows=len(inputs.rows),
            runtime_dependency_lock_sha256=runtime_lock,
            input_dependency_lock_sha256=input_lock,
            input_lock_compatible=input_lock == runtime_lock,
        )
        results = []
        for family in ("random_forest", "hist_gradient_boosting"):
            candidates = [
                name
                for name in archive.receipts
                if name.startswith("backtest/comparison/models/")
                and name.endswith(family + ".json")
            ]
            name = max(candidates)
            with regular_file(args.run_dir, name) as stream:
                raw = stream.read(128 * 1024**2 + 1)
            receipt = archive.receipts[name]
            if (len(raw), hashlib.sha256(raw).hexdigest()) != (receipt.size_bytes, receipt.sha256):
                raise ValueError("runtime_acceptance_model_changed_after_archive_verification")
            cold = time.monotonic()
            pipeline = ModelPipeline.model_validate_json(json.dumps(decode_model_json(raw)))
            adapter = ForecastAdapter(pipeline)
            model_lock = pipeline.descriptor.code.dependency_lock_sha256
            if (
                model_lock != runtime_lock
                and "archived_model_dependency_lock_mismatch" not in blockers
            ):
                blockers.append("archived_model_dependency_lock_mismatch")
            cold_seconds = time.monotonic() - cold
            if inputs.as_of_time <= pipeline.descriptor.preprocessing.fold.selection_cutoff:
                raise ValueError("runtime_acceptance_origin_before_model_selection")
            batch = time.monotonic()
            values = adapter.infer(
                list(inputs.rows), policy=inputs.feature_manifest.descriptor.resolved_policy
            )
            batch_seconds = time.monotonic() - batch
            # Independent public diagnostic entry point on the original pinned dataset agrees.
            if values != adapter.predict(
                list(inputs.rows), feature_set_id=pipeline.descriptor.feature_set_id
            ):
                raise ValueError("runtime_inference_and_original_adapter_disagree")
            results.append(
                {
                    "family": family,
                    "model_id": pipeline.model_id,
                    "model_sha256": receipt.sha256,
                    "model_bytes": len(raw),
                    "dependency_lock_sha256": model_lock,
                    "model_lock_compatible": model_lock == runtime_lock,
                    "cold_load_seconds": cold_seconds,
                    "batch_seconds": batch_seconds,
                    "rows": len(values),
                    "prediction_sha256": canonical_sha256(values),
                }
            )
        # Baseline recipe is a local diagnostic, not an approved capsule or registry version.
        recipe = {
            "format": "forecast-baseline-v1",
            "feature_set_id": inputs.feature_manifest.feature_set_id,
            "model": "seasonal_naive7",
            "policy": pipeline.descriptor.policy.baseline.model_dump(mode="json"),
            "selection_cutoff": pipeline.descriptor.preprocessing.fold.selection_cutoff.isoformat().replace(
                "+00:00", "Z"
            ),
        }
        baseline = BaselinePipeline.model_validate_json(
            json.dumps(dict(recipe, model_id="model-sha256-" + canonical_sha256(recipe)))
        )
        contexts = {h.content_sha256(): h for h in inputs.histories}
        baseline_values = predict_examples(
            baseline,
            [BaselineInput(row=r, history=contexts[r.history_context_sha256]) for r in inputs.rows],
        )
        results.append(
            {
                "family": "baseline",
                "recipe": "seasonal_naive7",
                "rows": len(baseline_values),
                "prediction_sha256": canonical_sha256(baseline_values),
            }
        )
        report.update(
            adapter_results=results,
            total_seconds=time.monotonic() - started,
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            publication_blockers=blockers,
        )
    else:
        from update_forecast_job_contracts import main as contracts

        saved = sys.argv
        try:
            sys.argv = [saved[0], "--check"]
            if contracts() != 0:
                return 1
        finally:
            sys.argv = saved
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {"status": "passed", "report": str(args.output), "published_forecast_outputs": 0}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
