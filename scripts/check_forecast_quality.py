"""Quality component gate; optional full segment/calibration replay from a temporal backtest."""

import argparse
import hashlib
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from retailops_ai.forecasting.quality import build_quality, load_quality, verify_quality
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.quality_metrics import (
    SegmentAccumulator,
    assess_segment,
    residual_rank,
)

ROOT = Path(__file__).resolve().parents[1]


def hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in root.rglob("*"):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(1024**2):
                    digest.update(block)
            result[path.relative_to(root).as_posix()] = digest.hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-dir", type=Path)
    parser.add_argument("--backtest-dir", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/forecast-quality.json")
    args = parser.parse_args()
    if (args.feature_dir is None) != (args.backtest_dir is None):
        parser.error("feature-dir and backtest-dir must be supplied together")
    started = time.monotonic()
    config = (ROOT / "contracts/forecast/v1/quality.default.json").read_bytes()
    policy = QualityPolicy.model_validate_json(config)
    report: dict[str, object]
    with tempfile.TemporaryDirectory(prefix="forecast-quality-check-") as temporary:
        if args.feature_dir is None:
            zero, baseline = (
                SegmentAccumulator(policy.nominal_coverage),
                SegmentAccumulator(policy.nominal_coverage),
            )
            zero.add(0, 100.0, (), (0.0, 200.0))
            baseline.add(0, 0.0, (), (0.0, 200.0))
            metric = zero.result(
                "fold", "development_holdout", "validation_selected", "global", "all"
            )
            gate = assess_segment(
                metric,
                baseline.result(
                    "fold", "development_holdout", "validation_baseline", "global", "all"
                ),
                policy,
                retained=False,
            )
            if (
                metric.point.mae != 100
                or metric.point.wape is not None
                or gate["status"] != "not_ready"
                or residual_rank(50, policy) != 46
            ):
                raise ValueError("quality_zero_or_calibration_component_failed")
            report = {
                "status": "passed",
                "scope": "quality/interval components only",
                "temporal_qualification": "not_ready",
                "zero_example": metric.model_dump(mode="json"),
                "zero_gate": gate,
            }
        else:
            feature_dir, backtest_dir = args.feature_dir, args.backtest_dir
            parents = {"features": hashes(feature_dir), "backtest": hashes(backtest_dir)}
            output_root = args.output_root or Path(temporary) / "quality"
            print(
                "Quality: segments and validation-only interval calibration",
                file=sys.stderr,
                flush=True,
            )
            directory = build_quality(feature_dir, backtest_dir, output_root, policy)
            print(
                "Quality: independent metric/calibration/gate replay, no retraining",
                file=sys.stderr,
                flush=True,
            )
            manifest = verify_quality(directory, feature_dir, backtest_dir)
            before = hashes(directory)
            print("Quality: immutable rerun", file=sys.stderr, flush=True)
            if build_quality(
                feature_dir, backtest_dir, output_root, policy
            ) != directory or before != hashes(directory):
                raise ValueError("quality_immutable_rerun_failed")
            if parents != {"features": hashes(feature_dir), "backtest": hashes(backtest_dir)}:
                raise ValueError("quality_parent_mutation")
            if load_quality(directory) != manifest:
                raise ValueError("quality_read_receipt_changed")
            metrics = json.loads((directory / "segments.json").read_bytes())["rows"]
            gates = json.loads((directory / "gates.json").read_bytes())["rows"]
            report = {
                "status": "passed",
                "scope": "AI 04.7 development quality protocol, not model release",
                "quality_id": manifest.quality_id,
                "descriptor": manifest.descriptor.model_dump(mode="json"),
                "receipts": {
                    name: receipt.model_dump(mode="json")
                    for name, receipt in manifest.receipts.items()
                },
                "pooled_global_metrics": [
                    m for m in metrics if m["fold"] == "pooled" and m["dimension"] == "global"
                ],
                "gate_reason_counts": dict(
                    Counter(
                        reason
                        for g in gates
                        for reason in (*g["not_ready_reasons"], *g["failed_reasons"])
                    )
                ),
                "nonpassing_gates": [g for g in gates if g["status"] != "passed"],
                "independent_parent_backed_report_replay": True,
                "repeatable_and_immutable": True,
                "parents_unchanged": True,
                "new_model_fits": 0,
            }
        report.update(
            policy=policy.model_dump(mode="json"),
            policy_file_sha256=hashlib.sha256(config).hexdigest(),
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
