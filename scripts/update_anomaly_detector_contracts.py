"""Generate or check the additive anomaly detector mechanics contracts."""

import argparse
import json
from pathlib import Path

from retailops_ai.anomaly_detectors.contract import (
    FitPolicy,
    ModelManifest,
    Prediction,
    RunManifest,
)
from retailops_ai.anomaly_detectors.protocol import Protocol
from retailops_ai.anomaly_detectors.rows import MultiscaleRow

ROOT = Path(__file__).resolve().parents[1] / "src/retailops_ai/anomaly_detectors/contracts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, model in (
        ("protocol", Protocol),
        ("policy", FitPolicy),
        ("model", ModelManifest),
        ("run", RunManifest),
        ("prediction", Prediction),
        ("multiscale_row", MultiscaleRow),
    ):
        payload = (
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
        path = ROOT / (name + ".schema.json")
        if args.check:
            if not path.is_file() or path.read_bytes() != payload:
                raise ValueError("anomaly_detector_contract_drift: " + name)
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
