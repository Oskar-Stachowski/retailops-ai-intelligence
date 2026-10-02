"""Verify or import a complete AI 04 v12 campaign using its installed, pinned wheel."""

import argparse
import http.client
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from retailops_ai.model_lifecycle.v12_clone_tracking import CloneTracking
from retailops_ai.model_lifecycle.v12_evidence import load_evidence
from retailops_ai.model_lifecycle.v12_mlflow import LocalTracking, import_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--verifier-python", type=Path, required=True)
    parser.add_argument("--mlflow-port", type=int, default=5010)
    parser.add_argument("--clone-artifact-root", type=Path)
    parser.add_argument("--minimum-free-gib", type=int, default=50)
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
            if args.minimum_free_gib < 0:
                raise ValueError("v12_import_space_policy")
            minimum = args.minimum_free_gib * 1024**3
            client: LocalTracking
            reserve: Callable[[], None] | None = None
            if args.clone_artifact_root:
                client = CloneTracking(
                    args.clone_artifact_root, args.mlflow_port, minimum_free_bytes=minimum
                )
            else:
                client = LocalTracking(args.mlflow_port)

                def check_reserve() -> None:
                    if (
                        shutil.disk_usage(evidence.root).free
                        - sum(ref.size_bytes for ref in evidence.files.values())
                        < minimum
                    ):
                        raise ValueError("v12_import_would_exhaust_disk_reserve")

                reserve = check_reserve
            result = import_evidence(evidence, client, args.work_dir, before_upload=reserve)
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
