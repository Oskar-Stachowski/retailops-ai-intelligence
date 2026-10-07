"""Complete populations and train-only fitting on explicitly controlled typed records."""

import hashlib
import sqlite3
import stat
import zlib
from contextlib import closing

import numpy as np
import pytest
from test_forecast_features import SERIES
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_fit_data as data
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitPlan
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.pipeline import environment_lock


def fit_plan(**changes):
    return CampaignForecastFitPlan(
        source_recipe_sha256="1" * 64,
        export_operation_id="controlled-export",
        family="rf",
        initialization_seed=137,
        worker_environment_lock_sha256=hashlib.sha256(environment_lock()).hexdigest(),
        resources=CampaignGenerationResources(
            wall_seconds=600,
            tree_rss_bytes=1024**3,
            scratch_bytes=1024**3,
            minimum_free_disk_bytes=1024**3,
            minimum_available_memory_bytes=1024**3,
        ),
        **changes,
    )


@pytest.fixture
def indexed(stored_control, population, timeline, monkeypatch, tmp_path):
    root, manifest = stored_control
    _, records, _ = population
    histories = {
        r.history_context_sha256: OriginFeatures(
            timeline, make_origin(r.forecast_origin.date())
        ).history(SERIES)
        for r in records
    }
    monkeypatch.setattr(
        data,
        "input_models",
        lambda path, name: iter(records if name == "features" else histories.values()),
    )
    with closing(sqlite3.connect(tmp_path / "fit.sqlite")) as db:
        counts = data.index_roles(db, root, manifest, fit_plan())
        yield db, root, manifest, counts


def test_all_eligible_keys_are_shared_by_tree_and_partial_horizon_tensorflow(indexed, tmp_path):
    db, _, manifest, counts = indexed
    assert counts == {role: manifest.descriptor.populations[role].row_count for role in data.ROLES}
    # Preserve a partial horizon group. A closed target remains present but masked.
    key, feature, body = db.execute(
        "SELECT key,feature,body FROM examples WHERE role='train' ORDER BY key LIMIT 1"
    ).fetchone()
    example = data.PhysicalForecastExample.model_validate_json(zlib.decompress(body))
    outcome = example.outcome.model_copy(update={"eligible": False, "reasons": ("closed_target",)})
    example = example.model_copy(update={"outcome": outcome})
    db.execute(
        "UPDATE examples SET eligible=0,body=? WHERE key=?",
        (zlib.compress(canonical_bytes(example.model_dump(mode="json"))), key),
    )
    encoding = data.fit_encoding(db, fit_plan())
    outputs = {}
    for kind, factory in (("tree", data.tree_matrices), ("tf", data.tensorflow_matrices)):
        target = tmp_path / kind
        target.mkdir(mode=0o700)
        outputs[kind] = factory(db, "train", encoding, fit_plan(), target)
        assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in target.iterdir())
    expected_keys = [key for key, _, _ in data.rows(db, "train")]
    expected_digest = hashlib.sha256(b"".join(key + b"\n" for key in expected_keys)).hexdigest()
    assert outputs["tree"]["keys_sha256"] == outputs["tf"]["keys_sha256"] == expected_digest
    masks = np.load(tmp_path / "tf/train-mask.npy", allow_pickle=False)
    assert masks.shape == (1, 14) and masks.sum() == len(expected_keys) == encoding.train_rows
    assert masks.sum() < 14
    assert outputs["tree"]["rows"] == outputs["tf"]["eligible_rows"] == len(expected_keys)


def test_early_stopping_changes_never_fit_train_encoding_or_vocabulary(indexed):
    db, _, _, _ = indexed
    original = data.fit_encoding(db, fit_plan())
    for key, row, _ in list(data.rows(db, "early_stopping")):
        raw = row.model_dump(mode="json")
        for value in raw["values"]:
            if value["name"] == "brand":
                value["value"] = "unseen-development-brand"
            elif value["name"] == "origin_lag_1_units":
                value["value"] = 987654321
                value["status"] = "available"
                value["source_available_at"] = raw["forecast_origin"]
                value["reason"] = None
        changed = InputRow.model_validate_json(canonical_bytes(raw))
        db.execute(
            "UPDATE examples SET feature=? WHERE key=?",
            (zlib.compress(canonical_bytes(changed.model_dump(mode="json"))), key),
        )
    for table in ("numeric_values", "categories", "history_values"):
        db.execute("DROP TABLE " + table)  # noqa: S608 -- fixed test table names
    assert data.fit_encoding(db, fit_plan()) == original
    row = next(data.rows(db, "early_stopping"))[1]
    values = data.transform(row, original)
    index = original.output_columns.index("brand__unknown")
    assert values[index] == 1.0 and values[index - 1] == 0.0
    assert "unseen-development-brand" not in str(original.model_dump())


@pytest.mark.parametrize(
    "role", ["tune", "calibration", "development_evaluation", "final_evaluation", "purged"]
)
def test_nontraining_roles_are_rejected_before_materializing_matrices(indexed, tmp_path, role):
    db, _, _, _ = indexed
    encoding = data.fit_encoding(db, fit_plan())
    for factory in (data.tree_matrices, data.tensorflow_matrices):
        with pytest.raises(SnapshotError, match="training_or_early_stopping_only"):
            factory(db, role, encoding, fit_plan(), tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_population_and_matrix_budgets_reject_the_whole_fit_without_sampling(indexed, tmp_path):
    db, _, _, _ = indexed
    with pytest.raises(SnapshotError, match="train_population_limit"):
        data.fit_encoding(db, fit_plan(max_train_rows=1))
    for table in ("numeric_values", "categories", "history_values"):
        db.execute("DROP TABLE " + table)  # noqa: S608 -- fixed test table names
    encoding = data.fit_encoding(db, fit_plan())
    for factory in (data.tree_matrices, data.tensorflow_matrices):
        with pytest.raises(SnapshotError, match="matrix_budget"):
            factory(db, "train", encoding, fit_plan(max_matrix_bytes=1024), tmp_path)
    assert not list(tmp_path.glob("train-*.npy"))


def test_role_checksum_and_feature_receipts_are_verified_before_fitting(indexed, tmp_path):
    _, root, manifest, _ = indexed
    with (root / "train.jsonl").open("ab") as stream:
        stream.write(b" ")
    with closing(sqlite3.connect(tmp_path / "wrong.sqlite")) as db:
        with pytest.raises(SnapshotError, match="role_record_limit"):
            data.index_roles(db, root, manifest, fit_plan())
