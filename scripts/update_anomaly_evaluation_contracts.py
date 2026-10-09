"""Generate or check the independent offline business-truth evaluation contracts."""

import argparse
import json
from pathlib import Path

from retailops_ai.anomaly_evaluation.contract import EvaluationPolicy, OrdinaryTruth, Truth
from retailops_ai.evaluation_campaign.campaign_anomaly_truth import OrdinarySourceVerification

ROOT = Path(__file__).resolve().parents[1] / "src/retailops_ai/anomaly_evaluation/contracts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in (
        ("policy", EvaluationPolicy),
        ("truth", Truth),
        ("ordinary-truth.v1", OrdinaryTruth),
        ("ordinary-source-verification.v1", OrdinarySourceVerification),
    ):
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
        target = ROOT / f"{name}.schema.json"
        if args.check:
            if not target.is_file() or target.read_bytes() != raw:
                raise ValueError("anomaly_evaluation_contract_drift")
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
