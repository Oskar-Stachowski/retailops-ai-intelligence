"""Verify a completed v12 export and execute a private, label-free offline load/predict smoke."""

import argparse
import json
import subprocess
from pathlib import Path

from retailops_ai.forecast_jobs.inputs import verify_inputs_package
from retailops_ai.forecast_jobs.v12_runtime import load_v12


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--verifier-python", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--fold", required=True)
    parser.add_argument("--recipe-id", required=True)
    parser.add_argument("--inputs-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        inputs = verify_inputs_package(args.inputs_dir)
        loaded = load_v12(
            args.run_dir,
            args.verifier_python,
            run_id=args.run_id,
            cohort_id=args.cohort_id,
            fold=args.fold,
            recipe_id=args.recipe_id,
        )
        result = loaded.predict(inputs)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('{"error":"v12_runtime_acceptance_failed"}')
        return 1
    print(
        json.dumps(
            dict(
                status="offline_load_predict_passed",
                pin=result.pin.model_dump(mode="json"),
                profile_id=result.profile_id,
                rows=len(result.predictions),
                predictions_sha256=result.predictions_sha256,
                cold_load_seconds=result.cold_load_seconds,
                compute_seconds=result.compute_seconds,
                peak_rss_bytes=result.peak_rss_bytes,
                model_refits=result.model_refits,
                source_generation=result.source_generation,
                serving_eligible=result.serving_eligible,
                published_forecast_outputs=result.published_forecast_outputs,
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
