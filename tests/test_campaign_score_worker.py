"""Full-key scoring on typed controls with explicit fake models; not native model evidence."""

import copy
import hashlib

import numpy as np
import pytest
from pydantic import ValidationError
from test_campaign_fit import encoding
from test_campaign_fit_data import fit_plan
from test_campaign_score_data import score_indexed as score_indexed
from test_campaign_score_data import score_plan
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_score_data as data
from retailops_ai.evaluation_campaign import campaign_score_worker as worker
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitReceipt
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastRawPrediction,
)
from retailops_ai.evaluation_campaign.campaign_score_metrics import RawMetrics
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.source_snapshot.files import SnapshotError


class ConstantTree:
    def __init__(self, value):
        self.value = value

    def matrix(self, matrix):
        return np.full(len(matrix), self.value, dtype=np.float64)


def controlled_models():
    state = encoding().model_copy(update={"target_scale": 10.0})
    encodings = {f: state for f in FAMILIES}
    models = {
        "rf:mean": ConstantTree(7.0),
        "hgb:mean": ConstantTree(6.0),
        "hgb:median": ConstantTree(5.0),
        "tensorflow": lambda matrix, training=False: np.tile(
            np.asarray([3.0, 2.0], dtype=np.float32), (len(matrix), 14, 1)
        ),
    }
    return encodings, models


def test_all_six_models_preserve_partial_windows_exclusions_heads_and_original_units(score_indexed):
    db, _, _, counts = score_indexed
    encodings, models = controlled_models()
    windows = list(data.windows(db))
    assert counts["eligible_rows"] > 0
    key = next(
        key for records, _ in windows for key, _, example in records if example.outcome.eligible
    )
    for records, _ in windows:
        for i, (record_key, row, example) in enumerate(records):
            if record_key == key:
                excluded = example.outcome.model_copy(
                    update={"eligible": False, "reasons": ("closed_target",)}
                )
                records[i] = record_key, row, example.model_copy(update={"outcome": excluded})
    predictions = worker.predict_batch(windows, encodings, models)
    assert len(predictions) == counts["rows"]
    assert {membership_key(p) for p in predictions} == {
        key for records, _ in windows for key, _, _ in records
    }
    assert sum(p.eligible for p in predictions) == counts["eligible_rows"] - 1
    excluded = next(p for p in predictions if membership_key(p) == key)
    assert not excluded.eligible and all(p == worker.EMPTY for p in excluded.values)
    for prediction in predictions:
        if prediction.eligible:
            assert prediction.values[3].mean == 7.0 and prediction.values[3].median is None
            assert prediction.values[4].mean == 6.0 and prediction.values[4].median == 5.0
            assert prediction.values[5].mean == 30.0 and prediction.values[5].median == 20.0
            assert all(p.interval is None for p in prediction.values[3:])


def test_changed_outcomes_never_change_inputs_or_predictions(score_indexed):
    db, _, _, _ = score_indexed
    windows = list(data.windows(db))
    encodings, models = controlled_models()
    first = worker.predict_batch(windows, encodings, models)
    changed = copy.deepcopy(windows)
    for records, _ in changed:
        for index, (key, row, example) in enumerate(records):
            if example.outcome.eligible:
                label = example.outcome.label.model_copy(update={"observed_sales_units": 99999})
                records[index] = (
                    key,
                    row,
                    example.model_copy(
                        update={"outcome": example.outcome.model_copy(update={"label": label})}
                    ),
                )
    second = worker.predict_batch(changed, encodings, models)
    assert [p.values for p in first] == [p.values for p in second]
    assert any(a.example_sha256 != b.example_sha256 for a, b in zip(first, second, strict=True))


@pytest.mark.parametrize(
    "model,value", [("rf:mean", -1), ("hgb:mean", float("nan")), ("hgb:median", float("inf"))]
)
def test_invalid_native_functionals_fail_the_whole_batch(score_indexed, model, value):
    encodings, models = controlled_models()
    models[model] = ConstantTree(value)
    with pytest.raises(SnapshotError, match="invalid_tree_output"):
        worker.predict_batch(list(data.windows(score_indexed[0])), encodings, models)


def fake_fits(dataset_id):
    state = encoding()
    files = {"controlled.json": "a" * 64}
    return {
        family: CampaignForecastFitReceipt(
            protocol_sha256="b" * 64,
            operation_id="controlled-fit-" + family,
            reservation_id="campaign-operation-" + "1" * 32,
            plan=fit_plan().model_copy(update={"family": family}),
            export_receipt_sha256="c" * 64,
            dataset_id=dataset_id,
            runtime_code_sha256="d" * 64,
            train_keys_sha256=state.train_keys_sha256,
            early_stopping_keys_sha256="e" * 64,
            train_eligible_rows=state.train_rows,
            early_stopping_eligible_rows=2,
            encoding_sha256=state.content_sha256(),
            model_artifact_sha256=canonical_sha256(files),
            model_artifact_bytes=1,
            artifact_files=files,
            worker_evidence={"mocked": True},
        )
        for family in FAMILIES
    }


def test_float32_overflow_is_rejected_before_framework_inference(score_indexed):
    encodings, models = controlled_models()
    encodings["tensorflow"] = encodings["tensorflow"].model_copy(update={"history_spread": 1e-100})

    def forbidden(*args, **kwargs):
        raise AssertionError("nonfinite inputs must not reach framework inference")

    models["tensorflow"] = forbidden
    with pytest.raises(SnapshotError, match="nonfinite_tensorflow_inputs"):
        worker.predict_batch(list(data.windows(score_indexed[0])), encodings, models)


def test_different_fitted_target_population_is_rejected_before_framework_loading(monkeypatch):
    fits = fake_fits("ai09-physical-forecast-sha256-" + "1" * 64)
    monkeypatch.setattr(worker, "_versions", lambda plan: None)
    monkeypatch.setattr(worker, "_verify_bundle_content", lambda *args: None)

    def controlled_encoding(bundle, name, *args):
        assert name == "encoding.json", "model loading must follow the complete population check"
        state = encoding()
        if bundle.name == "tensorflow":
            state = state.model_copy(update={"train_labels_sha256": "f" * 64})
        return canonical_bytes(state.model_dump(mode="json"))

    monkeypatch.setattr(worker, "read_bytes", controlled_encoding)
    request = {
        "bundles": {f: "/controlled/" + f for f in FAMILIES},
        "fits": {f: fits[f].model_dump(mode="json") for f in FAMILIES},
    }
    with pytest.raises(SnapshotError, match="fitted_training_population_mismatch"):
        worker.load_models(request)


def test_streamed_artifact_is_sorted_complete_and_costed_without_label_refit(
    score_indexed, tmp_path, monkeypatch
):
    db, _, manifest, counts = score_indexed
    root = tmp_path / "worker"
    root.mkdir(mode=0o700)
    with worker._index(root / "score.sqlite", score_plan().max_index_bytes) as copied:
        db.backup(copied)
    write(root / "prepare.json", counts)
    monkeypatch.setattr(worker, "load_models", lambda request: controlled_models())
    fits = fake_fits(manifest.dataset_id)
    request = {
        "exported": {"scope": "controlled-fixture"},
        "fits": {f: fits[f].model_dump(mode="json") for f in FAMILIES},
    }
    result = worker.predict(root, request, score_plan())
    assert result["rows"] == counts["rows"] and result["eligible_rows"] == counts["eligible_rows"]
    lines = (root / "bundle/predictions.jsonl").read_bytes().splitlines()
    rows = [CampaignForecastRawPrediction.model_validate_json(line) for line in lines]
    keys = [membership_key(row) for row in rows]
    assert keys == sorted(set(keys))
    assert (
        hashlib.sha256(b"".join(key + b"\n" for key in keys)).hexdigest() == result["keys_sha256"]
    )
    metrics = read(root / "bundle/metrics.json")
    assert metrics["scope"] == "raw_development_diagnostic_not_qualification"
    assert not metrics["quality_qualified"] and not metrics["final_test_accessed"]
    assert metrics["segments"][0]["eligible_rows"] == counts["eligible_rows"]
    assert metrics["segments"][0]["models"]["rf_mean"]["median"]["mae"] is None
    assert metrics["segments"][0]["models"]["tensorflow"]["mean"]["complete"]


def test_all_zero_and_all_excluded_are_undefined_ratios_not_ideal_results(score_indexed):
    db, _, _, _ = score_indexed
    encodings, models = controlled_models()
    predictions = worker.predict_batch(list(data.windows(db)), encodings, models)
    zero = RawMetrics()
    for row in predictions:
        zero.add(row, 0 if row.eligible else None)
    point = zero.result()["segments"][0]["models"]["tensorflow"]["mean"]
    assert point["wape"] is None and point["normalized_bias"] is None
    assert point["mae"] == 30.0 and point["zero_actual_excess_units"] > 0
    excluded = RawMetrics()
    for row in predictions:
        raw = row.model_dump(mode="json") | {
            "eligible": False,
            "exclusion_reasons": ["censored_label"],
            "values": [worker.EMPTY.model_dump(mode="json")] * 6,
        }
        excluded.add(CampaignForecastRawPrediction.model_validate_json(canonical_bytes(raw)), None)
    point = excluded.result()["segments"][0]["models"]["tensorflow"]["mean"]
    assert not point["complete"] and point["mae"] is None and point["wape"] is None


def test_raw_wire_rejects_invented_rf_median_and_predictions_for_excluded_keys(score_indexed):
    db, _, _, _ = score_indexed
    encodings, models = controlled_models()
    prediction = next(
        p for p in worker.predict_batch(list(data.windows(db)), encodings, models) if p.eligible
    )
    raw = prediction.model_dump(mode="json")
    raw["values"][3]["median"] = 1.0
    with pytest.raises(ValidationError, match="functionals"):
        CampaignForecastRawPrediction.model_validate_json(canonical_bytes(raw))
    raw["values"][3]["median"] = None
    raw.update(eligible=False, exclusion_reasons=["censored_label"])
    with pytest.raises(ValidationError, match="excluded_key"):
        CampaignForecastRawPrediction.model_validate_json(canonical_bytes(raw))
