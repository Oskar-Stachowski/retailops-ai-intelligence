"""Inference parity, disjoint calibration role and honest missing-segment metrics."""

from copy import deepcopy
from dataclasses import replace

import pytest
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_tensorflow_challenger import FEATURE_ID, SPLIT_ID
from test_tensorflow_challenger import development as development

from retailops_ai.evaluation_campaign.campaign_forecast_inference import (
    InferenceRecord,
    InferenceWindow,
)
from retailops_ai.evaluation_campaign.comparison import forecast_key_bytes
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.evaluation_campaign.portfolio_assessment import (
    add_intervals,
    fit_intervals,
    metrics,
    tensorflow_vector,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.dataset import fit_normalization, transform_window


def test_label_free_inference_exactly_matches_fitted_transform_for_partial_horizons(development):
    fold, train, validation = development
    state = fit_normalization(train, fold=fold, feature_set_id=FEATURE_ID, split_id=SPLIT_ID)
    for original in (*train, *validation):
        for window in (original, replace(original, samples=original.samples[::3])):
            features = InferenceWindow(
                tuple(
                    InferenceRecord(forecast_key_bytes(s.row), s.row, s.membership.eligible)
                    for s in window.samples
                ),
                window.history,
            )
            assert tensorflow_vector(features, state) == transform_window(window, state)
            # Features contain no target or role; changing calibration/final outcomes
            # cannot feed the model or fit a different vocabulary/normalization.
            assert not hasattr(features.records[0], "actual")


def rows(role="calibration"):
    return [
        {
            "key": f"{horizon}:{i}",
            "role": role,
            "actual": 3,
            "horizon_days": horizon,
            "exclusion_reasons": [],
            "groups": {
                "horizon": str(horizon),
                "volume": "low",
                "channel": "web",
                "category": "food",
            },
            "predictions": {
                name: {"mean": 4.0, "median": None if name == "rf_mean" else 2.0, "interval": None}
                for name in MODELS
            },
        }
        for horizon in range(1, 15)
        for i in range(100)
    ]


def test_calibration_fits_every_horizon_and_never_calls_rf_mean_a_median():
    original = rows()
    fitted = fit_intervals(original)
    assert fitted["status"] == "fitted"
    assert fitted["models"]["tensorflow"]["horizons"]["14"] == {
        "rows": 100,
        "eligible_rows": 100,
        "rank": 91,
        "radius": 1.0,
        "reasons": [],
    }
    points = add_intervals(original[0], fitted)
    assert points["rf_mean"].median is None
    assert points["rf_mean"].interval.lower == 3
    assert points["tensorflow"].interval.upper == 3
    assert original[0]["predictions"]["tensorflow"]["interval"] is None


@pytest.mark.parametrize("role", ["train", "validation", "final_test"])
def test_other_roles_cannot_fit_calibration(role):
    with pytest.raises(SnapshotError, match="calibration_role_required"):
        fit_intervals(rows(role))


def test_missing_horizon_cannot_be_accepted_or_used_for_final():
    source = [r for r in rows() if r["horizon_days"] != 14]
    fitted = fit_intervals(source)
    assert fitted["status"] == "not_ready"
    assert fitted["models"]["tensorflow"]["horizons"]["14"]["radius"] is None
    with pytest.raises(SnapshotError, match="calibration_incomplete"):
        add_intervals(source[0], fitted)


def test_final_reports_null_ratios_and_missing_required_groups_without_false_pass():
    calibration = fit_intervals(rows())
    source = rows("final_test")
    for row in source:
        row["actual"] = 0
    before = deepcopy(calibration)
    report = metrics(source, calibration, {"volume": ["zero", "low", "medium", "high"]})
    model = report["models"]["tensorflow"]
    assert model["global"]["candidate"]["mean"]["wape"] is None
    assert model["global"]["candidate"]["mean"]["mae"] == 4
    assert model["global"]["candidate"]["interval"]["coverage"] == 0
    assert model["segments"]["volume"]["high"]["status"] == "not_ready"
    assert model["segments"]["volume"]["high"]["eligible_rows"] == 0
    assert calibration == before


def test_duplicate_final_keys_or_wrong_role_are_refused():
    calibration = fit_intervals(rows())
    source = rows("final_test")
    with pytest.raises(SnapshotError, match="duplicate_final_key"):
        metrics([*source, source[0]], calibration, {})
    with pytest.raises(SnapshotError, match="final_role_required"):
        metrics(rows(), calibration, {})


def test_label_free_wire_preserves_partial_horizon_keys_and_rejects_missing_predictions(
    development, tmp_path
):
    from retailops_ai.data_contracts.identity import canonical_bytes
    from retailops_ai.evaluation_campaign.portfolio_data_io import (
        inference_windows,
        scored_rows,
        write_population,
    )

    _, _, validation = development
    records = [
        (s.row, w.history, s.label.observed_sales_units, s.membership.reasons)
        for w in validation
        for s in w.samples[::3]
    ]
    manifest = write_population(tmp_path, records, role="calibration")
    restored = list(inference_windows(tmp_path))
    assert [r.key for w in restored for r in w.records] == [
        forecast_key_bytes(r[0]) for r in records
    ]
    assert manifest["rows"] == len(records)
    with (tmp_path / "predictions.jsonl").open("wb") as stream:
        for row, _, _, reasons in records[:-1]:
            stream.write(
                canonical_bytes(
                    {
                        "key": forecast_key_bytes(row).decode(),
                        "predictions": {
                            name: {
                                "mean": None if reasons else 1.0,
                                "median": None if reasons or name == "rf_mean" else 1.0,
                                "interval": None,
                            }
                            for name in MODELS
                        },
                    }
                )
                + b"\n"
            )
    with pytest.raises(SnapshotError, match="key_mismatch"):
        scored_rows(tmp_path)


def test_population_rejects_mixed_history_before_model_inference(development, tmp_path):
    from retailops_ai.evaluation_campaign.portfolio_data_io import write_population

    _, train, validation = development
    sample = validation[0].samples[0]
    with pytest.raises(SnapshotError, match="population_binding"):
        write_population(
            tmp_path,
            [
                (
                    sample.row,
                    train[0].history,
                    sample.label.observed_sales_units,
                    sample.membership.reasons,
                )
            ],
            role="final_test",
        )
