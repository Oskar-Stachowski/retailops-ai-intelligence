"""Compare saved models on seed-42 validation only; never fit or score a final window."""

import argparse
import json
from pathlib import Path
from typing import Any

from retailops_ai.anomaly_evaluation.evaluator import evaluate
from retailops_ai.anomaly_evaluation.quality import combine
from retailops_ai.anomaly_evaluation.source_truth import TRUTH_POLICY, source_truth
from retailops_ai.anomaly_portfolio.artifacts import immutable, immutable_json
from retailops_ai.anomaly_portfolio.cli import prepared, protocol
from retailops_ai.anomaly_portfolio.model import load, score
from retailops_ai.source_snapshot.files import canonical_json, decode_json, read_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fit-root", type=Path, required=True)
    parser.add_argument("--demand-receipt", type=Path, required=True)
    parser.add_argument("--physical-receipt", type=Path, required=True)
    parser.add_argument("--demand-bundle", type=Path, required=True)
    parser.add_argument("--physical-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fit = decode_json(read_bytes(args.fit_root, "fit_manifest.json", 4 * 1024**2))
    comparison: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for scenario in ("demand", "physical"):
        receipt_path = getattr(args, scenario + "_receipt")
        bundle_path = getattr(args, scenario + "_bundle")
        frame = prepared(receipt_path)
        split = protocol(frame)
        points = list(frame.points())
        receipt = decode_json(read_bytes(receipt_path.parent, receipt_path.name, 1024**2))
        public = decode_json(
            read_bytes(
                Path(receipt["parents"][3]) / "snapshot", "snapshot_manifest.json", 8 * 1024**2
            )
        )["source"]
        parameters = public["descriptor"]["resolved_parameters"]
        if parameters["seed"] != 42 or parameters["profile"] not in {
            "ai-07-portfolio-v1",
            "ai-07-portfolio-v2",
            "ai-07-portfolio-v3",
            "ai-07-portfolio-v4",
        }:
            raise ValueError("anomaly_validation_development_profile_only")
        bundle = decode_json(read_bytes(bundle_path.parent, bundle_path.name, 1024**2))
        truth = source_truth(
            Path(bundle["private_scenario"]), public, split.scopes, split.validation
        )
        immutable_json(
            args.output / scenario / "validation_truth.json", truth.model_dump(mode="json")
        )
        for record in fit["models"]:
            model = load(Path(record["model_path"]), record["model_sha256"])
            for family in ("seasonal_residual", "isolation_forest"):
                decisions = score(
                    model,
                    points,
                    split.scopes,
                    split.validation,
                    family,
                    "validation",
                    split.selection_cutoff,
                )
                path = args.output / scenario / model.detector_id / family
                immutable(
                    path / "decisions.jsonl",
                    b"".join(canonical_json(d.model_dump(mode="json")) + b"\n" for d in decisions),
                )
                report = evaluate(
                    decisions,
                    truth,
                    split.validation,
                    split.selection_cutoff,
                    source_dataset_id=public["dataset_id"],
                )
                immutable_json(path / "evaluation.json", report)
                case = {
                    "seed": 42,
                    "scenario": scenario,
                    "source_dataset_id": public["dataset_id"],
                    "report": report,
                }
                comparison.setdefault((model.detector_id, family), []).append(case)
                print(
                    json.dumps(
                        {
                            "stage": "validation",
                            "scenario": scenario,
                            "detector_id": model.detector_id,
                            "family": family,
                            "precision": report["descriptor"]["observation"]["precision"]["value"],
                            "episode_recall": report["descriptor"]["episode"]["recall"]["value"],
                        }
                    ),
                    flush=True,
                )
    results = []
    for record in fit["models"]:
        for family in ("seasonal_residual", "isolation_forest"):
            summary = combine(comparison[record["detector_id"], family])
            results.append({"model": record, "family": family, "validation": summary})
    output = {
        "version": "anomaly-validation-comparison-1.0.0",
        "source_seed": 42,
        "truth_policy": TRUTH_POLICY,
        "results": results,
        "final_test": "not_scored",
    }
    immutable_json(args.output / "comparison.json", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
