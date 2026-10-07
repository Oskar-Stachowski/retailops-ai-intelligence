"""Generate or check frozen portfolio model, decision and capacity contracts."""

import argparse
import json
from pathlib import Path

from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.anomaly_evaluation.quality import QualityPolicy
from retailops_ai.anomaly_portfolio.lifecycle_contract import Qualification, Release, Request
from retailops_ai.anomaly_portfolio.model import Model
from retailops_ai.anomaly_portfolio.protocol import PortfolioProtocol
from retailops_ai.anomaly_portfolio.serving_contract import Item, Page, Query
from retailops_ai.full_raw_dq.contract import PortfolioBinding
from retailops_ai.full_raw_dq.store import Manifest

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for relative, model in (
        ("src/retailops_ai/anomaly_portfolio/contracts/model.schema.json", Model),
        ("src/retailops_ai/anomaly_portfolio/contracts/decision.schema.json", Decision),
        ("src/retailops_ai/anomaly_portfolio/contracts/protocol.schema.json", PortfolioProtocol),
        ("src/retailops_ai/anomaly_portfolio/contracts/qualification.schema.json", Qualification),
        ("src/retailops_ai/anomaly_portfolio/contracts/release.schema.json", Release),
        ("src/retailops_ai/anomaly_portfolio/contracts/request.schema.json", Request),
        ("src/retailops_ai/anomaly_portfolio/contracts/item.schema.json", Item),
        ("src/retailops_ai/anomaly_portfolio/contracts/page.schema.json", Page),
        ("src/retailops_ai/anomaly_portfolio/contracts/query.schema.json", Query),
        ("src/retailops_ai/anomaly_evaluation/contracts/quality.schema.json", QualityPolicy),
        ("contracts/raw_dq/v2/binding_v21.schema.json", PortfolioBinding),
        ("contracts/raw_dq/v2/manifest_v21.schema.json", Manifest),
    ):
        target = ROOT / relative
        raw = (
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    **model.model_json_schema(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()
        if args.check:
            if not target.is_file() or target.read_bytes() != raw:
                raise ValueError("anomaly_portfolio_contract_drift")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
