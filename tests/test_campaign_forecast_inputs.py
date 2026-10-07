"""Inference uses the train representation and restores native forecast units without labels."""

import zlib

import numpy as np
import pytest
from test_campaign_fit_data import fit_plan
from test_campaign_fit_data import indexed as indexed
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.evaluation_campaign import campaign_fit_data as data
from retailops_ai.evaluation_campaign import campaign_forecast_inputs as inputs
from retailops_ai.forecasting.features_contract import HistoryContext
from retailops_ai.source_snapshot.files import SnapshotError


def window(db, role):
    rows = tuple(row for _, row, _ in data.rows(db, role, eligible=False))
    history = HistoryContext.model_validate_json(
        zlib.decompress(
            db.execute(
                "SELECT body FROM histories WHERE hash=?", (rows[0].history_context_sha256,)
            ).fetchone()[0]
        )
    )
    return rows, history


def test_inference_reuses_complete_training_and_early_stopping_vectors(indexed, tmp_path):
    db, _, _, _ = indexed
    encoding = data.fit_encoding(db, fit_plan())
    original = encoding.model_dump(mode="json")
    for role in data.ROLES:
        rows, history = window(db, role)
        root = tmp_path / role
        root.mkdir(mode=0o700)
        data.tensorflow_matrices(db, role, encoding, fit_plan(), root)
        np.testing.assert_array_equal(
            np.asarray(
                inputs.tensorflow_vector(reversed(rows), history, encoding), dtype=np.float32
            ),
            np.load(root / (role + "-x.npy"), allow_pickle=False)[0],
        )
        trees = root / "tree"
        trees.mkdir(mode=0o700)
        data.tree_matrices(db, role, encoding, fit_plan(), trees)
        expected = np.load(trees / (role + "-x.npy"), allow_pickle=False)
        eligible = tuple(row for _, row, _ in data.rows(db, role))
        np.testing.assert_array_equal(
            np.asarray([inputs.tree_vector(row, encoding) for row in eligible]), expected
        )
    assert encoding.model_dump(mode="json") == original


def test_partial_horizon_preserves_position_and_empty_horizon_padding(indexed):
    db, _, _, _ = indexed
    state = data.fit_encoding(db, fit_plan())
    rows, history = window(db, "early_stopping")
    chosen = next(row for row in rows if row.horizon_days == 2)
    vector = inputs.tensorflow_vector([chosen], history, state)
    width = len(state.output_columns) + 1
    assert vector[84 : 84 + width] == (0.0,) * width
    assert vector[84 + width : 84 + 2 * width] == inputs.tree_vector(chosen, state)
    assert len(vector) == state.tensorflow_width


@pytest.mark.parametrize(
    "fault", ["duplicate", "foreign_series", "foreign_origin", "wrong_history_hash", "empty"]
)
def test_invalid_prediction_window_is_rejected_before_any_model(indexed, fault):
    db, _, _, _ = indexed
    state = data.fit_encoding(db, fit_plan())
    rows, history = window(db, "early_stopping")
    values = [rows[0]]
    if fault == "duplicate":
        values *= 2
    elif fault == "foreign_series":
        values = [rows[0].model_copy(update={"product_id": "foreign-product"})]
    elif fault == "foreign_origin":
        values = [
            rows[0].model_copy(
                update={"forecast_origin": history.forecast_origin.replace(year=2025)}
            )
        ]
    elif fault == "wrong_history_hash":
        values = [rows[0].model_copy(update={"history_context_sha256": "0" * 64})]
    else:
        values = []
    with pytest.raises(SnapshotError, match="campaign_forecast_"):
        inputs.tensorflow_vector(values, history, state)


def test_tensorflow_native_units_keep_mean_median_and_all_fourteen_horizons(indexed):
    db, _, _, _ = indexed
    state = data.fit_encoding(db, fit_plan()).model_copy(update={"target_scale": 3.0})
    raw = np.asarray([[h, h + 0.5] for h in range(1, 15)], dtype=np.float32)
    values = inputs.tensorflow_functionals(raw, state)
    assert len(values) == 14
    for horizon, forecast in enumerate(values, 1):
        assert forecast.mean == horizon * 3.0
        assert forecast.median == (horizon + 0.5) * 3.0
        assert forecast.interval is None


@pytest.mark.parametrize(
    "fault",
    ["missing_horizon", "extra_horizon", "missing_head", "extra_head", "nonfinite", "negative"],
)
def test_corrupt_tensorflow_output_cannot_become_a_forecast(indexed, fault):
    db, _, _, _ = indexed
    state = data.fit_encoding(db, fit_plan())
    raw = [[1.0, 2.0] for _ in range(14)]
    if fault == "missing_horizon":
        raw.pop()
    elif fault == "extra_horizon":
        raw.append([1.0, 2.0])
    elif fault == "missing_head":
        raw[0].pop()
    elif fault == "extra_head":
        raw[0].append(3.0)
    elif fault == "nonfinite":
        raw[0][0] = float("nan")
    else:
        raw[0][0] = -1.0
    with pytest.raises(SnapshotError, match="campaign_forecast_tensorflow_output"):
        inputs.tensorflow_functionals(raw, state)
