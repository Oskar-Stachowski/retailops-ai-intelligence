"""Bounded offline evaluation of complete saved detector decisions."""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from retailops_ai.anomaly_detectors.contract import Prediction
from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.anomaly_evaluation.contract import Decision, Truth
from retailops_ai.anomaly_evaluation.evaluator import evaluate
from retailops_ai.source_snapshot.files import canonical_json, read_bytes
from retailops_ai.source_snapshot.importer import write_private


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--window-from", type=date.fromisoformat, required=True)
    parser.add_argument("--window-to", type=date.fromisoformat, required=True)
    parser.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-dataset-id", required=True)
    args = parser.parse_args()
    if args.output.absolute() in {args.predictions.absolute(), args.truth.absolute()}:
        parser.error("Evaluation output must be separate from its immutable inputs")
    predictions = [
        Decision.model_validate_json(line)
        if b'"anomaly-portfolio-decision-1.0.0"' in line
        else Prediction.model_validate_json(line)
        for line in read_bytes(
            args.predictions.parent, args.predictions.name, 128 * 1024**2
        ).splitlines()
    ]
    truth = Truth.model_validate_json(read_bytes(args.truth.parent, args.truth.name, 16 * 1024**2))
    report = evaluate(
        predictions,
        truth,
        Window(start=args.window_from, end=args.window_to),
        args.as_of,
        source_dataset_id=args.source_dataset_id,
    )
    output = canonical_json(report) + b"\n"
    # Exclusive creation makes an existing report immutable, including on retry.
    if args.output.exists():
        if read_bytes(args.output.parent, args.output.name, 32 * 1024**2) != output:
            raise ValueError("anomaly_evaluation_immutable_conflict")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_private(args.output, output)
    sys.stdout.write(
        canonical_json(
            {"evaluation_id": report["evaluation_id"], "qualification": "not_qualified"}
        ).decode()
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
