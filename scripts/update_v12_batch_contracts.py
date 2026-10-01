"""Generate/check private v12 computation queue contracts; existing HTTP schemas stay closed."""

import argparse
from pathlib import Path

from retailops_ai.data_contracts.common import Contract
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecast_jobs.contracts import BatchRequest, QueuePolicy
from retailops_ai.forecast_jobs.v12_batch import V12BatchReceipt, V12BatchRun

ROOT = Path(__file__).resolve().parents[1] / "contracts/forecast_jobs/v12"
SCHEMAS: dict[str, type[Contract]] = {
    "request": BatchRequest,
    "policy": QueuePolicy,
    "run": V12BatchRun,
    "receipt": V12BatchReceipt,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in SCHEMAS.items():
        path = ROOT / (name + ".schema.json")
        raw = canonical_bytes(model.model_json_schema()) + b"\n"
        if args.check:
            if not path.is_file() or path.read_bytes() != raw:
                print("v12_batch_contract_snapshot_mismatch")
                return 1
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    print("V12 batch contracts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
