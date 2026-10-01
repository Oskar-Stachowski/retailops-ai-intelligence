"""Verify or import a complete AI 04 v12 campaign using its installed, pinned wheel."""

import argparse
import http.client
import json
import subprocess
from pathlib import Path

from retailops_ai.model_lifecycle.v12_evidence import load_evidence
from retailops_ai.model_lifecycle.v12_mlflow import LocalTracking, import_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--verifier-python", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--verify-timeout", type=int, default=3600)
    parser.add_argument("--work-dir", type=Path, default=Path(".local/mlflow-v12-imports"))
    args = parser.parse_args()
    try:
        evidence = load_evidence(
            args.run_dir, args.verifier_python, timeout_seconds=args.verify_timeout
        )
        if args.verify_only:
            result = {
                "status": "verified",
                "original_run_id": evidence.run_id,
                "forecast_model_status": evidence.handoff["forecast_model_status"],
                "artifact_files": len(evidence.files),
                "serving_eligible": False,
            }
        else:
            result = import_evidence(evidence, LocalTracking(), args.work_dir)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        http.client.HTTPException,
    ):
        print('{"error":"mlflow_v12_import_failed"}')
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
