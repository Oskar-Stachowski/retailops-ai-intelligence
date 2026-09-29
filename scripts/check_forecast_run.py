"""Fast AI 04.8 contract gate; optionally verify a full archived historical run."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from retailops_ai.forecasting.run import verify_run
from retailops_ai.forecasting.run_contract import ForecastRunManifest

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/forecast-run.json")
    args = parser.parse_args()
    schema = ForecastRunManifest.model_json_schema()
    frozen = json.loads((ROOT / "contracts/forecast/v1/run_manifest.schema.json").read_text())
    schema.update({"$schema": frozen["$schema"], "$id": frozen["$id"]})
    if schema != frozen:
        raise ValueError("run_contract_schema_stale")
    report: dict[str, Any] = {
        "status": "passed",
        "scope": "AI 04.8 evidence export contract"
        if args.run_dir is None
        else "full archived run",
        "forecast_model_status": "not_ready",
        "mlflow_runs_created": 0,
        "aws_calls": 0,
    }
    if args.run_dir is not None:
        manifest = verify_run(args.run_dir)
        report.update(
            run_id=manifest.run_id,
            descriptor=manifest.descriptor.model_dump(mode="json"),
            artifact_files=len(manifest.receipts),
            artifact_bytes=sum(value.size_bytes for value in manifest.receipts.values()),
            run_manifest_sha256=hashlib.sha256(
                (args.run_dir / "run_manifest.json").read_bytes()
            ).hexdigest(),
            run_status=manifest.run_status,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "passed", "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
