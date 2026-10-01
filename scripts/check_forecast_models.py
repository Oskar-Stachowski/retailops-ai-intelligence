"""Offline learned-model gate: component smoke or verified temporal comparison/replay."""

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from retailops_ai.forecasting.model_contract import LearnedEstimator, ModelPolicy
from retailops_ai.forecasting.model_trees import TreePredictor
from retailops_ai.forecasting.models import (
    LEARNED,
    build_comparison,
    fit_worker,
    verify_comparison,
)

ROOT = Path(__file__).resolve().parents[1]


def hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-dir", type=Path)
    parser.add_argument("--split-dir", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/forecast-models.json")
    args = parser.parse_args()
    if (args.feature_dir is None) != (args.split_dir is None):
        parser.error("feature-dir and split-dir must be supplied together")
    started = time.monotonic()
    policy = ModelPolicy()
    with tempfile.TemporaryDirectory(prefix="forecast-models-check-") as temporary:
        workspace = Path(temporary).resolve()
        if args.feature_dir is None:
            # Numeric component exercise, explicitly not a qualified temporal dataset.
            rng = np.random.default_rng(42)
            x = rng.normal(size=(128, 4))
            x[:, -1] = np.tile(np.arange(1, 15), 10)[:128]
            y = np.maximum(0, np.round(10 + x[:, 0] * 2 + x[:, -1] * 0.1))
            resources = {}
            for family in LEARNED:
                estimator, receipt = fit_worker(x, y, family, policy)
                restored = LearnedEstimator.model_validate_json(estimator.model_dump_json())
                np.testing.assert_array_equal(
                    TreePredictor(estimator).matrix(x), TreePredictor(restored).matrix(x)
                )
                resources[family] = receipt.model_dump(mode="json")
            report = {
                "status": "passed",
                "scope": "numeric component fit/export/JSON replay; not temporal qualification",
                "temporal_qualification": "not_ready",
                "policy": policy.model_dump(mode="json"),
                "resources": resources,
                "component_rows": len(y),
            }
        else:
            feature_dir, split_dir = args.feature_dir, args.split_dir
            before = {"features": hashes(feature_dir), "split": hashes(split_dir)}
            output_root = args.output_root or workspace / "comparisons"
            print("Temporal models: fit and shared evaluation", file=sys.stderr, flush=True)
            directory = build_comparison(feature_dir, split_dir, output_root, policy)
            print("Temporal models: independent retraining/replay", file=sys.stderr, flush=True)
            manifest = verify_comparison(directory, feature_dir, split_dir)
            output_before = hashes(directory)
            print("Temporal models: immutable rerun", file=sys.stderr, flush=True)
            if (
                build_comparison(feature_dir, split_dir, output_root, policy) != directory
                or hashes(directory) != output_before
            ):
                raise ValueError("model_comparison_repeatability_failed")
            if before != {"features": hashes(feature_dir), "split": hashes(split_dir)}:
                raise ValueError("model_comparison_input_mutation")
            if manifest.descriptor.status != "passed":
                raise ValueError("model_comparison_execution_not_ready")
            report = {
                "status": "passed",
                "scope": "AI 04.5 temporal training/comparison; diagnostic, not model release",
                "comparison_id": manifest.comparison_id,
                "descriptor": manifest.descriptor.model_dump(mode="json"),
                "predictions": manifest.predictions.model_dump(mode="json"),
                "pipelines": {
                    name: receipt.model_dump(mode="json")
                    for name, receipt in manifest.pipelines.items()
                },
                "resources": {
                    name: receipt.model_dump(mode="json")
                    for name, receipt in manifest.resources.items()
                },
                "repeatable_and_immutable": True,
                "parents_unchanged": True,
                "full_retraining_replay": True,
            }
        report.update(
            seconds=round(time.monotonic() - started, 3),
            aws_calls=0,
            forecast_model_status="not_ready",
            portfolio_final_test="not_included_not_opened",
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "passed", "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
