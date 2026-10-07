"""Generate the supported v2 event schema from its canonical ML API payload model."""

import argparse
from pathlib import Path

from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.intelligence_events.contracts import ForecastGenerated
from retailops_ai.intelligence_events.model_contracts import AnomalyDetected, StockoutRiskScored

ROOT = Path(__file__).resolve().parents[1] / "contracts/events/v2"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = {
        "forecast_generated.schema.json": ForecastGenerated.model_json_schema(),
        "anomaly_detected.schema.json": AnomalyDetected.model_json_schema(),
        "stockout_risk_scored.schema.json": StockoutRiskScored.model_json_schema(),
        "registry.json": {
            "contract_version": "2.0.0",
            "topic": "retailops.intelligence.v2",
            "source": "retailops-ai",
            "supported_schema_versions": ["2.0"],
            "event_type_schemas": {
                "forecast_generated": "forecast_generated.schema.json",
                "anomaly_detected": "anomaly_detected.schema.json",
                "stockout_risk_scored": "stockout_risk_scored.schema.json",
            },
            "reserved_event_types": [
                "recommendation_generated",
            ],
            "payload_owner": "retailops_ai.forecast_jobs.v12_read_contracts.V12ForecastItem",
            "payload_owners": {
                "forecast_generated": "retailops_ai.forecast_jobs.v12_read_contracts.V12ForecastItem",
                "anomaly_detected": "retailops_ai.anomaly_portfolio.serving_contract.Item",
                "stockout_risk_scored": "retailops_ai.stockout_runtime.public_contracts.RiskItem",
            },
            "native_identity_fields": {
                "anomaly_detected": sorted(Decision.model_fields),
            },
            "event_namespace": "b85cb398-e62a-5f70-91ed-6c0fbe868a50",
            "max_event_bytes": 32768,
            "legacy_v1": "unchanged",
        },
    }
    for name, document in files.items():
        path = ROOT / name
        raw = canonical_bytes(document) + b"\n"
        if args.check:
            if not path.is_file() or path.read_bytes() != raw:
                print("intelligence_event_contract_snapshot_mismatch")
                return 1
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    print("Intelligence v2 forecast, anomaly and physical stockout contracts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
