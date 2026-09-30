"""Generate/check forecast schemas and frozen task, feature and development split configs."""

import argparse
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from retailops_ai.forecasting.backtest_contract import BacktestManifest, BacktestPolicy
from retailops_ai.forecasting.contract import CalendarManifest, OriginWindow, TaskConfig
from retailops_ai.forecasting.evaluation_contract import (
    BaselinePolicy,
    BaselinePrediction,
    EvaluationManifest,
)
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow, PanelPolicy
from retailops_ai.forecasting.manifest_contract import (
    FeatureManifest,
    FeaturePolicy,
    LabelPoint,
    Membership,
    SplitManifest,
    SplitPolicy,
)
from retailops_ai.forecasting.model_contract import (
    ForecastValue,
    ModelPipeline,
    ModelPolicy,
    ModelPrediction,
    ModelRunManifest,
)
from retailops_ai.forecasting.preprocessing import FittedState
from retailops_ai.forecasting.quality_contract import QualityManifest, QualityPolicy, SegmentMetric
from retailops_ai.forecasting.quality_v2_contract import ProtocolObservation, QualityPolicyV2
from retailops_ai.forecasting.remediation_contract import RemediationManifest, RemediationPolicy
from retailops_ai.forecasting.remediation_v2_contract import (
    RemediationManifest as RemediationV2Manifest,
)
from retailops_ai.forecasting.remediation_v2_contract import (
    RemediationPolicy as RemediationV2Policy,
)
from retailops_ai.forecasting.run_contract import ForecastRunManifest
from retailops_ai.forecasting.splits import default_split

ROOT = Path(__file__).resolve().parents[1]


def artifacts() -> dict[Path, object]:
    result: dict[Path, object] = {}
    for name, model in (
        ("task", TaskConfig),
        ("calendar_manifest", CalendarManifest),
        ("history_context", HistoryContext),
        ("input_row", InputRow),
        ("panel_policy", PanelPolicy),
        ("feature_policy", FeaturePolicy),
        ("feature_manifest", FeatureManifest),
        ("split_policy", SplitPolicy),
        ("split_manifest", SplitManifest),
        ("label_point", LabelPoint),
        ("membership", Membership),
        ("preprocessing", FittedState),
        ("baseline_policy", BaselinePolicy),
        ("baseline_prediction", BaselinePrediction),
        ("evaluation_manifest", EvaluationManifest),
        ("model_policy", ModelPolicy),
        ("model_pipeline", ModelPipeline),
        ("model_prediction", ModelPrediction),
        ("model_comparison", ModelRunManifest),
        ("forecast_value", ForecastValue),
        ("backtest_policy", BacktestPolicy),
        ("backtest_manifest", BacktestManifest),
        ("quality_policy", QualityPolicy),
        ("quality_manifest", QualityManifest),
        ("segment_metric", SegmentMetric),
        ("run_manifest", ForecastRunManifest),
        ("remediation_policy", RemediationPolicy),
        ("remediation_manifest", RemediationManifest),
        ("remediation_v2_policy", RemediationV2Policy),
        ("remediation_v2_manifest", RemediationV2Manifest),
    ):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:forecast:{name}:1.0.0"
        result[ROOT / f"contracts/forecast/v1/{name}.schema.json"] = schema
    result[ROOT / "src/retailops_ai/forecasting/task.default.json"] = TaskConfig().model_dump(
        mode="json"
    )
    result[ROOT / "contracts/forecast/v1/features.default.json"] = FeaturePolicy().model_dump(
        mode="json"
    )
    result[ROOT / "contracts/forecast/v1/baselines.default.json"] = BaselinePolicy().model_dump(
        mode="json"
    )
    result[ROOT / "contracts/forecast/v1/models.default.json"] = ModelPolicy().model_dump(
        mode="json"
    )
    result[ROOT / "contracts/forecast/v1/backtest.default.json"] = BacktestPolicy().model_dump(
        mode="json"
    )
    result[ROOT / "contracts/forecast/v1/quality.default.json"] = QualityPolicy().model_dump(
        mode="json"
    )
    result[ROOT / "contracts/forecast/v1/remediation.default.json"] = (
        RemediationPolicy().model_dump(mode="json")
    )
    result[ROOT / "contracts/forecast/v1/remediation_v2.default.json"] = (
        RemediationV2Policy().model_dump(mode="json")
    )
    for v2_name, v2_model in (
        ("quality_policy", QualityPolicyV2),
        ("quality_observation", ProtocolObservation),
    ):
        schema = v2_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:forecast:{v2_name}:2.0.0"
        result[ROOT / f"contracts/forecast/v2/{v2_name}.schema.json"] = schema
    result[ROOT / "contracts/forecast/v2/quality.default.json"] = QualityPolicyV2().model_dump(
        mode="json"
    )
    calendar = SimpleNamespace(
        descriptor=SimpleNamespace(
            origin_window=OriginWindow(start=date(2026, 5, 19), end=date(2026, 7, 17))
        )
    )
    result[ROOT / "contracts/forecast/v1/split.temporal.default.json"] = default_split(
        calendar
    ).model_dump(mode="json")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for path, value in artifacts().items():
        raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    if stale:
        print("Forecast contracts differ: " + ", ".join(stale))
        return 1
    print("Forecast contract snapshots checked." if args.check else "Forecast contracts generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
