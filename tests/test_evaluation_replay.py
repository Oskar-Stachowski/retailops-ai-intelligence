import io
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.evaluation_contract import BASELINES, BaselinePrediction
from retailops_ai.forecasting.manifest_contract import LabelPoint, Membership
from retailops_ai.forecasting.model_contract import LEARNED_NAMES, ModelPrediction
from retailops_ai.forecasting.quality_metrics import SegmentAccumulator
from retailops_ai.model_lifecycle import evaluation_replay as replay
from retailops_ai.model_lifecycle.evaluation_contracts import METHODS


@pytest.mark.parametrize(
    "mutation", [None, "forged_metric", "missing_prediction", "duplicate_prediction"]
)
def test_saved_point_replay_protects_metrics_coverage_and_excluded_scope(
    tmp_path, monkeypatch, mutation
):
    members, labels, predictions = [], [], []
    origin = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
    for role in ("validation", "development_holdout"):
        for product in ("p-101", "p-202"):
            eligible = product == "p-101"
            grain = dict(
                product_id=product,
                selling_location_id="s-1",
                channel="store",
                forecast_origin=origin.isoformat(),
                business_timezone="UTC",
                cutoff_policy="end_of_day_second_v1",
                target_date="2026-08-01",
                horizon_days=1,
            )
            label = LabelPoint.model_validate_json(
                json.dumps(
                    dict(
                        **grain,
                        fold="fold-01",
                        role=role,
                        knowledge_cutoff="2026-09-01T00:00:00Z",
                        status="eligible" if eligible else "censored",
                        observed_sales_units=3 if eligible else None,
                        label_available_at="2026-08-01T23:59:59Z" if eligible else None,
                        source_record_sha256="a" * 64 if eligible else None,
                        source_record_id="one" if eligible else None,
                        version=1 if eligible else None,
                        reason=None if eligible else "missing_or_unavailable",
                    )
                )
            )
            labels.append(label)
            reasons = [] if eligible else ["censored_label"]
            members.append(
                Membership.model_validate_json(
                    json.dumps(
                        dict(
                            **grain,
                            fold="fold-01",
                            role=role,
                            eligible=eligible,
                            reasons=reasons,
                            label_content_sha256=canonical_sha256(label.model_dump(mode="json")),
                        )
                    )
                )
            )
            for name in (*BASELINES, *LEARNED_NAMES):
                raw = dict(
                    **grain,
                    fold="fold-01",
                    role=role,
                    model=name,
                    eligible=eligible,
                    exclusion_reasons=reasons,
                )
                if name in LEARNED_NAMES:
                    raw.update(
                        model_id="model-sha256-" + "a" * 64,
                        predicted_units=2.0 if eligible else None,
                        prediction_kind="out_of_time_diagnostic" if eligible else "excluded",
                        training_knowledge_cutoff=(origin - timedelta(days=1)).isoformat(),
                    )
                    row = ModelPrediction.model_validate_json(json.dumps(raw))
                else:
                    raw["estimate"] = (
                        dict(predicted_units=2.0, history_dates=["2026-07-31"], reason=None)
                        if eligible
                        else None
                    )
                    row = BaselinePrediction.model_validate_json(json.dumps(raw))
                predictions.append(canonical_bytes(row.model_dump(mode="json")) + b"\n")
    segments = []
    for role in ("validation", "development_holdout"):
        for method in sorted(METHODS):
            a = SegmentAccumulator(0.9)
            a.add(3, 2.0, (), None)
            a.add(None, None, ("censored_label",), None)
            segments.append(
                a.result("pooled", role, method, "global", "all").model_dump(mode="json")
            )
    if mutation == "forged_metric":
        segments[0]["point"].update(mae=5.0, absolute_error_sum=5.0, wape=5 / 3)
    elif mutation == "missing_prediction":
        predictions.pop()
    elif mutation == "duplicate_prediction":
        predictions.append(predictions[0])
    monkeypatch.setattr(
        replay,
        "load_split",
        lambda _: SimpleNamespace(
            tables={"memberships": SimpleNamespace(content_sha256="b" * 64), "labels": None}
        ),
    )
    monkeypatch.setattr(
        replay,
        "load_comparison",
        lambda _: SimpleNamespace(
            descriptor=SimpleNamespace(
                selections=[
                    SimpleNamespace(
                        fold="fold-01",
                        status="selected",
                        selected="random_forest",
                        baseline="moving_average",
                    )
                ]
            )
        ),
    )
    monkeypatch.setattr(
        replay,
        "iter_table",
        lambda _, kind, *__: iter(members if kind == "memberships" else labels),
    )
    monkeypatch.setattr(replay, "read_bytes", lambda *_: json.dumps({"rows": segments}).encode())

    @contextmanager
    def saved_predictions(*_):
        yield io.BytesIO(b"".join(predictions))

    monkeypatch.setattr(replay, "regular_file", saved_predictions)
    if mutation:
        with pytest.raises(
            ValueError,
            match={
                "forged_metric": "point_metric_replay_mismatch",
                "missing_prediction": "method_coverage_mismatch",
                "duplicate_prediction": "duplicate_prediction",
            }[mutation],
        ):
            replay.replay(tmp_path)
    else:
        scope, count, _, metrics = replay.replay(tmp_path)
        assert scope.product_ids == ("p-101", "p-202") and count == 4
        assert len(metrics) == 14 and all(
            m.total_rows == 2
            and m.excluded_rows == 1
            and m.point.mae == 1.0
            and m.point.wape == 1 / 3
            for m in metrics
        )
