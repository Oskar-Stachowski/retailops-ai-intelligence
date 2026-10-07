"""Controlled complete role records; no project campaign or quality claims."""

import hashlib
import sqlite3
from contextlib import closing

import pytest
from pydantic import ValidationError
from test_campaign_fit_data import fit_plan
from test_forecast_features import SERIES
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_score_data as data
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScorePlan
from retailops_ai.evaluation_campaign.physical_forecast import _index
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.source_snapshot.files import SnapshotError


def score_plan(**changes):
    value = dict(
        source_recipe_sha256="1" * 64,
        export_operation_id="controlled-export",
        fit_operation_ids={f: "controlled-fit-" + f for f in ("rf", "hgb", "tensorflow")},
        role="tune",
        worker_environment_lock_sha256=fit_plan().worker_environment_lock_sha256,
        resources=fit_plan().resources,
    )
    return CampaignForecastScorePlan(**(value | changes))


@pytest.fixture
def score_indexed(stored_control, population, timeline, monkeypatch, tmp_path):
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
    with closing(_index(tmp_path / "score.sqlite", score_plan().max_index_bytes)) as db:
        counts = data.index_role(db, root, manifest, score_plan())
        db.commit()
        yield db, root, manifest, counts


def test_exact_selected_role_keys_and_checksums_without_opening_other_labels(score_indexed):
    db, root, manifest, counts = score_indexed
    expected = manifest.descriptor.populations["tune"]
    assert counts["rows"] == expected.row_count
    assert counts["eligible_rows"] == expected.eligible_rows
    assert counts["keys_sha256"] == expected.keys_sha256
    assert counts["role_population_sha256"] == expected.sha256
    assert (
        counts["role_population_sha256"]
        == hashlib.sha256((root / "tune.jsonl").read_bytes()).hexdigest()
    )
    windows = list(data.windows(db))
    assert sum(len(records) for records, _ in windows) == expected.row_count
    assert all(
        example.membership.role == "tune" for records, _ in windows for _, _, example in records
    )


@pytest.mark.parametrize(
    "role", ["train", "early_stopping", "development_evaluation", "final_evaluation", "purged"]
)
def test_raw_role_plan_cannot_open_training_or_evaluation_holdouts(role):
    with pytest.raises(ValidationError):
        score_plan(role=role)


@pytest.mark.parametrize("mutation", ["missing-family", "duplicate-fit", "model-order", "ready"])
def test_model_inventory_and_permission_flags_are_frozen(mutation):
    value = score_plan().model_dump(mode="json")
    if mutation == "missing-family":
        del value["fit_operation_ids"]["hgb"]
    elif mutation == "duplicate-fit":
        value["fit_operation_ids"]["hgb"] = value["fit_operation_ids"]["rf"]
    elif mutation == "model-order":
        value["model_order"].reverse()
    else:
        value["stage_ready"] = True
    with pytest.raises(ValidationError):
        CampaignForecastScorePlan.model_validate_json(canonical_bytes(value))


@pytest.mark.parametrize("mutation", ["checksum", "duplicate", "population", "index"])
def test_incomplete_or_over_budget_role_is_rejected_whole(score_indexed, tmp_path, mutation):
    _, root, manifest, _ = score_indexed
    plan = score_plan()
    if mutation == "checksum":
        with (root / "tune.jsonl").open("ab") as stream:
            stream.write(b" ")
    elif mutation == "duplicate":
        rows = (root / "tune.jsonl").read_bytes().splitlines(keepends=True)
        rows[1] = rows[0]
        (root / "tune.jsonl").write_bytes(b"".join(rows))
    elif mutation == "population":
        plan = score_plan(max_rows=1)
    else:
        plan = score_plan(max_index_bytes=4096)
    with closing(sqlite3.connect(tmp_path / "failed.sqlite")) as db:
        with pytest.raises((SnapshotError, sqlite3.Error)):
            data.index_role(db, root, manifest, plan)
