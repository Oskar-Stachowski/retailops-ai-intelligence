"""Generate/check the reviewed physical stockout queue and read contracts."""

import argparse
from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.stockout_jobs.batch import StockoutOutput
from retailops_ai.stockout_jobs.contracts import StockoutJobRun, StockoutRequest, StockoutRun
from retailops_ai.stockout_jobs.read_contracts import (
    StockoutAttempts,
    StockoutQuery,
    StockoutRiskPage,
)
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs

ROOT = Path(__file__).resolve().parents[1] / "contracts/stockout_jobs/v1"
MODELS = dict(
    request=StockoutRequest,
    run=StockoutRun,
    output=StockoutOutput,
    inputs=PreparedStockoutInputs,
    job_run=StockoutJobRun,
    attempts=StockoutAttempts,
    read_query=StockoutQuery,
    read_page=StockoutRiskPage,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in MODELS.items():
        path = ROOT / (name + ".schema.json")
        raw = canonical_bytes(model.model_json_schema()) + b"\n"
        if args.check:
            if not path.is_file() or path.read_bytes() != raw:
                print("stockout_job_contract_snapshot_mismatch")
                return 1
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    print("Stockout job contracts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
