"""Generate or check v12 inference/qualification/approval schemas separately from v1/offline."""

import argparse
from pathlib import Path

from retailops_ai.data_contracts.common import Contract
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecast_jobs.v12_inference_contracts import V12InferenceResult
from retailops_ai.model_lifecycle.v12_release_contracts import (
    V12ApprovalRequest,
    V12InferenceContext,
    V12InferenceRelease,
    V12Qualification,
    V12SourcePolicy,
)

ROOT = Path(__file__).resolve().parents[1] / "contracts/forecast/v12_inference"
SCHEMAS: dict[str, type[Contract]] = {
    "source_policy": V12SourcePolicy,
    "context": V12InferenceContext,
    "result": V12InferenceResult,
    "qualification": V12Qualification,
    "approval_request": V12ApprovalRequest,
    "release": V12InferenceRelease,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in SCHEMAS.items():
        raw = canonical_bytes(model.model_json_schema()) + b"\n"
        path = ROOT / (name + ".schema.json")
        if args.check:
            if not path.is_file() or path.read_bytes() != raw:
                print("v12_inference_contract_snapshot_mismatch")
                return 1
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    print("V12 inference contracts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
