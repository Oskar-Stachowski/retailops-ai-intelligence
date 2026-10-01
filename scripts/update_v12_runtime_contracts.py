"""Generate or check private v12 offline runtime schema snapshots, separate from serving v1."""

import argparse
from pathlib import Path

from retailops_ai.data_contracts.common import Contract
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecast_jobs.v12_contracts import V12Execution, V12RuntimePin, V12RuntimeResult

ROOT = Path(__file__).resolve().parents[1] / "contracts/forecast/v12_offline"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    schemas: dict[str, type[Contract]] = {
        "runtime_pin.schema.json": V12RuntimePin,
        "execution.schema.json": V12Execution,
        "runtime_result.schema.json": V12RuntimeResult,
    }
    for name, model in schemas.items():
        raw = canonical_bytes(model.model_json_schema()) + b"\n"
        path = ROOT / name
        if args.check:
            if not path.is_file() or path.read_bytes() != raw:
                print("v12_runtime_contract_snapshot_mismatch")
                return 1
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    print("V12 offline runtime contracts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
