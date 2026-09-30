"""Contract/components gate, or parent-backed verification of a remediation campaign."""

import argparse
import json
from pathlib import Path

from retailops_ai.forecasting.quality_metrics import SegmentAccumulator, assess_segment
from retailops_ai.forecasting.remediation import residual_quantiles, verify_remediation
from retailops_ai.forecasting.remediation_contract import RemediationManifest, RemediationPolicy
from retailops_ai.forecasting.remediation_v2_contract import (
    RemediationManifest as RemediationV2Manifest,
)
from retailops_ai.forecasting.remediation_v2_contract import (
    RemediationPolicy as RemediationV2Policy,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remediation-dir", type=Path)
    parser.add_argument("--feature-dir", type=Path)
    parser.add_argument("--backtest-dir", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/forecast-remediation.json")
    args = parser.parse_args()
    if any((args.remediation_dir, args.feature_dir, args.backtest_dir)) and not all(
        (args.remediation_dir, args.feature_dir, args.backtest_dir)
    ):
        parser.error("Full verification requires remediation, features and backtest directories.")
    for name, model in (
        ("remediation_policy", RemediationPolicy),
        ("remediation_manifest", RemediationManifest),
        ("remediation_v2_policy", RemediationV2Policy),
        ("remediation_v2_manifest", RemediationV2Manifest),
    ):
        schema = model.model_json_schema()
        frozen = json.loads((ROOT / f"contracts/forecast/v1/{name}.schema.json").read_bytes())
        schema.update({"$schema": frozen["$schema"], "$id": frozen["$id"]})
        if schema != frozen:
            raise ValueError("remediation_contract_schema_stale")
    policy = RemediationPolicy()
    if residual_quantiles([0.0] * 49, policy) is not None:
        raise ValueError("sparse_calibration_cannot_pass")
    candidate, baseline = SegmentAccumulator(0.9), SegmentAccumulator(0.9)
    for _ in range(100):
        candidate.add(0, 100.0, (), (0.0, 200.0))
        baseline.add(0, 0.0, (), (0.0, 0.0))
    selected = candidate.result("fold", "validation", "remediated", "global", "all")
    reference = baseline.result("fold", "validation", "baseline", "global", "all")
    if (
        selected.point.mae != 100
        or assess_segment(selected, reference, policy.quality, retained=False)["status"] == "passed"
    ):
        raise ValueError("zero_actuals_cannot_hide_overforecast")
    report: dict[str, object] = {
        "status": "passed",
        "scope": "remediation contract/components only",
        "quality_qualified": False,
        "aws_calls": 0,
    }
    if args.remediation_dir:
        manifest = verify_remediation(args.remediation_dir, args.feature_dir, args.backtest_dir)
        report.update(
            scope="full parent-backed remediation replay",
            remediation_id=manifest.remediation_id,
            quality_status=manifest.descriptor.quality_status,
            gate_counts=manifest.descriptor.gate_counts,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "passed", "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
