"""Complete controlled evaluation roles, separate actuals and honest final wire.

Declared-role fixtures do not establish journal authorization or fresh holdout.
The separate exposed-source test exercises the real final export and covariates.
"""

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from test_campaign_final_export import native_spec
from test_campaign_fit_data import fit_plan
from test_forecast_features import SERIES
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_forecast_source_replay import physical_parents as physical_parents
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.common import ForecastKey, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_evaluation_data as data
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
)
from retailops_ai.evaluation_campaign.campaign_final_contract import (
    CampaignFinalExportPlan,
    FinalForecastDescriptor,
    FinalForecastExample,
    FinalForecastManifest,
)
from retailops_ai.evaluation_campaign.final_forecast import (
    _build_final_forecast,
    verify_final_forecast,
)
from retailops_ai.evaluation_campaign.partitions import membership_key, runtime_pin
from retailops_ai.evaluation_campaign.physical_forecast import _index
from retailops_ai.evaluation_campaign.source_replay import (
    _open_verified_source_parent,
    physical_limits,
)
from retailops_ai.forecasting.contract import OriginWindow, make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.source_snapshot.files import SnapshotError


def evaluation_plan(role="development_evaluation", **changes):
    return CampaignForecastEvaluationPlan(
        **(
            dict(
                phase="final" if role == "final_test" else "development",
                role=role,
                source_recipe_sha256="1" * 64,
                export_operation_id="controlled-export",
                frozen_configuration_sha256="2" * 64,
                segment_policy_sha256="3" * 64,
                uncertainty_policy_sha256="4" * 64,
                worker_environment_lock_sha256=fit_plan().worker_environment_lock_sha256,
                resources=fit_plan().resources,
            )
            | changes
        )
    )


@pytest.fixture(params=["development_evaluation", "final_test"])
def evaluation_dataset(request, stored_control, population, timeline, monkeypatch, tmp_path):
    root, manifest = stored_control
    _, rows, _ = population
    plan = evaluation_plan(request.param)
    if plan.role == "final_test":
        # Explicit declared conversion of exposed control records. These are
        # neither a fresh project final dataset nor evidence of final access.
        examples = []
        for line in (root / "development_evaluation.jsonl").read_bytes().splitlines():
            value = json.loads(line)
            value["outcome"]["label"]["role"] = "final_evaluation"
            examples.append(
                FinalForecastExample.model_validate_json(
                    canonical_bytes(
                        {
                            "key": {k: value["membership"][k] for k in ForecastKey.model_fields},
                            "outcome": value["outcome"],
                        }
                    )
                )
            )
        selected = {membership_key(e.key) for e in examples}
        rows = [r for r in rows if membership_key(r) in selected]
        final_export = CampaignFinalExportPlan(
            source_recipe_sha256=plan.source_recipe_sha256,
            generation_operation_id="controlled-generation",
            origins=OriginWindow(
                start=rows[0].forecast_origin.date(), end=rows[0].forecast_origin.date()
            ),
            label_knowledge_cutoff=examples[0].outcome.label.knowledge_cutoff,
            prior_exposure_end=rows[0].forecast_origin.date() - timedelta(days=16),
            features=manifest.descriptor.recipe.features,
        )
        feature = manifest.descriptor.feature_descriptor.model_copy(update={"row_count": len(rows)})
        descriptor = FinalForecastDescriptor(
            recipe=final_export.bind(manifest.descriptor.recipe.source),
            runtime=manifest.descriptor.runtime,
            feature_set_id="features-sha256-" + canonical_sha256(feature.model_dump(mode="json")),
            feature_descriptor=feature,
            population=manifest.descriptor.populations["development_evaluation"].model_copy(
                update={
                    "size_bytes": len(
                        b"".join(
                            canonical_bytes(e.model_dump(mode="json")) + b"\n" for e in examples
                        )
                    ),
                    "sha256": hashlib.sha256(
                        b"".join(
                            canonical_bytes(e.model_dump(mode="json")) + b"\n" for e in examples
                        )
                    ).hexdigest(),
                }
            ),
            snapshot_inventory_sha256=manifest.descriptor.snapshot_inventory_sha256,
            curated_inventory_sha256=manifest.descriptor.curated_inventory_sha256,
            logical_curated_sha256=manifest.descriptor.logical_curated_sha256,
            observation_rows=manifest.descriptor.observation_rows,
            version_rows=manifest.descriptor.version_rows,
            version_inventory_sha256=manifest.descriptor.version_inventory_sha256,
        )
        manifest = FinalForecastManifest(
            dataset_id="ai09-final-forecast-sha256-"
            + canonical_sha256(descriptor.model_dump(mode="json")),
            descriptor=descriptor,
        )
        (root / "final_evaluation.jsonl").write_bytes(
            b"".join(canonical_bytes(e.model_dump(mode="json")) + b"\n" for e in examples)
        )
    histories = {
        r.history_context_sha256: OriginFeatures(
            timeline, make_origin(r.forecast_origin.date())
        ).history(SERIES)
        for r in rows
    }
    monkeypatch.setattr(
        data,
        "input_models",
        lambda path, name: iter(rows if name == "features" else histories.values()),
    )
    return root, manifest, plan, rows, histories


@pytest.fixture
def indexed(evaluation_dataset, tmp_path):
    root, manifest, plan, rows, histories = evaluation_dataset
    with (
        closing(_index(tmp_path / "inputs.sqlite", plan.max_index_bytes)) as db,
        closing(_index(tmp_path / "actuals.sqlite", plan.max_index_bytes)) as outcomes,
    ):
        counts = data.index_role(db, outcomes, root, manifest, plan)
        yield db, outcomes, root, manifest, plan, counts, rows, histories


def test_role_opened_once_no_other_outcomes_and_exact_complete_population(
    evaluation_dataset, tmp_path, monkeypatch
):
    root, manifest, plan, _, _ = evaluation_dataset
    opened = []
    original = data.regular_file

    def tracked(dataset, name):
        opened.append(name)
        return original(dataset, name)

    monkeypatch.setattr(data, "regular_file", tracked)
    with (
        closing(_index(tmp_path / "inputs.sqlite", plan.max_index_bytes)) as db,
        closing(_index(tmp_path / "actuals.sqlite", plan.max_index_bytes)) as outcomes,
    ):
        counts = data.index_role(db, outcomes, root, manifest, plan)
        expected_name = (
            "final_evaluation.jsonl"
            if plan.role == "final_test"
            else "development_evaluation.jsonl"
        )
        assert opened == [expected_name]
        assert (
            counts["role_population_sha256"]
            == hashlib.sha256((root / expected_name).read_bytes()).hexdigest()
        )
        windows = list(data.windows(db, plan))
        actuals = list(data.actuals(outcomes, plan))
        inputs = [record for window in windows for record in window.records]
        assert len(inputs) == len(actuals) == counts["rows"]
        assert {r.key: (r.example_sha256, r.eligible) for r in inputs} == {
            r.key: (r.example_sha256, r.eligible) for r in actuals
        }
        assert sum(r.eligible for r in actuals) == counts["eligible_rows"]
        assert all(a.actual is None for a in actuals if not a.eligible)
        assert {r.role for r in inputs} == {a.role for a in actuals} == {plan.role}
        assert all(1 <= len(window.records) <= 14 for window in windows)


def test_inference_connection_and_common_inputs_have_no_actuals(indexed):
    db, outcomes, _, _, plan, _, _, _ = indexed
    assert {row[1] for row in db.execute("PRAGMA table_info(examples)")}.isdisjoint(
        {"actual", "label", "body", "outcome"}
    )
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='actuals'").fetchone()
    assert not outcomes.execute("SELECT 1 FROM sqlite_master WHERE name='examples'").fetchone()
    # SQL trace observes all reads during common inference conversion. This
    # iterator consumes the entire role; no examples body/outcome is accessible.
    statements = []
    db.set_trace_callback(statements.append)
    windows = [window.inference() for window in data.windows(db, plan)]
    db.set_trace_callback(None)
    assert windows and not any("actual" in sql or "outcome" in sql for sql in statements)
    assert all(
        not hasattr(record, "actual") and not hasattr(record, "role")
        for window in windows
        for record in window.records
    )


def test_later_attachment_is_rejected_before_consuming_prepared_index(indexed):
    db, _, _, _, plan, _, _, _ = indexed
    db.execute("ATTACH DATABASE ':memory:' AS labels")
    with pytest.raises(SnapshotError, match="separate_actual_index_required"):
        next(data.windows(db, plan))


@pytest.mark.parametrize(
    "change",
    [
        "checksum",
        "duplicate",
        "record",
        "population",
        "index",
        "missing-feature",
        "duplicate-feature",
        "missing-history",
        "duplicate-history",
    ],
)
def test_incomplete_or_corrupt_population_never_yields_success(
    evaluation_dataset, tmp_path, monkeypatch, change
):
    root, manifest, plan, rows, histories = evaluation_dataset
    name = "final_evaluation.jsonl" if plan.role == "final_test" else "development_evaluation.jsonl"
    if change == "checksum":
        with (root / name).open("ab") as stream:
            stream.write(b" ")
    elif change == "duplicate":
        lines = (root / name).read_bytes().splitlines(keepends=True)
        lines[1] = lines[0]
        (root / name).write_bytes(b"".join(lines))
    elif change == "record":
        (root / name).write_bytes(b" " * 65537 + b"\n")
    elif change in {"population", "index"}:
        plan = evaluation_plan(
            plan.role, **({"max_rows": 1} if change == "population" else {"max_index_bytes": 4096})
        )
    else:
        required = {
            membership_key(
                ForecastKey.model_validate_json(
                    canonical_bytes(
                        {
                            k: json.loads(line)[
                                "key" if plan.role == "final_test" else "membership"
                            ][k]
                            for k in ForecastKey.model_fields
                        }
                    )
                )
            )
            for line in (root / name).read_bytes().splitlines()
        }
        selected = next(r for r in rows if membership_key(r) in required)
        altered_rows = (
            [r for r in rows if r is not selected]
            if change == "missing-feature"
            else [*rows, selected]
            if change == "duplicate-feature"
            else rows
        )
        altered_history = (
            []
            if change == "missing-history"
            else [*histories.values(), histories[selected.history_context_sha256]]
            if change == "duplicate-history"
            else histories.values()
        )
        monkeypatch.setattr(
            data,
            "input_models",
            lambda path, kind: iter(altered_rows if kind == "features" else altered_history),
        )
    with (
        closing(_index(tmp_path / "inputs.sqlite", plan.max_index_bytes)) as db,
        closing(_index(tmp_path / "actuals.sqlite", plan.max_index_bytes)) as outcomes,
    ):
        with pytest.raises((SnapshotError, sqlite3.Error, ValidationError)):
            data.index_role(db, outcomes, root, manifest, plan)


def test_changed_plan_cannot_consume_prepared_index(indexed):
    db, outcomes, _, _, plan, _, _, _ = indexed
    changed = plan.model_copy(update={"source_recipe_sha256": "f" * 64})
    for iterator in (data.windows(db, changed), data.actuals(outcomes, changed)):
        with pytest.raises(SnapshotError, match="index_scope_mismatch"):
            list(iterator)


def test_removed_rows_poison_both_complete_streams(indexed):
    db, outcomes, _, _, plan, _, _, _ = indexed
    db.execute("DELETE FROM examples WHERE key=(SELECT key FROM examples LIMIT 1)")
    outcomes.execute("DELETE FROM actuals WHERE key=(SELECT key FROM actuals LIMIT 1)")
    for iterator in (data.windows(db, plan), data.actuals(outcomes, plan)):
        with pytest.raises(SnapshotError, match="population_mismatch"):
            list(iterator)


def test_excluded_window_still_requires_matching_history(indexed):
    db, _, _, _, plan, _, _, histories = indexed
    wrong = next(iter(histories.values())).model_copy(update={"points": ()})
    db.execute("UPDATE examples SET eligible=0,reasons=?", (canonical_bytes(["closed_target"]),))
    import zlib

    db.execute(
        "UPDATE histories SET body=?",
        (zlib.compress(canonical_bytes(wrong.model_dump(mode="json"))),),
    )
    with pytest.raises(SnapshotError, match="window_history_binding"):
        list(data.windows(db, plan))


def test_final_source_mismatch_fails_before_role_read(evaluation_dataset, tmp_path, monkeypatch):
    root, manifest, plan, _, _ = evaluation_dataset
    if plan.role != "final_test":
        return
    changed = plan.model_copy(update={"source_recipe_sha256": "f" * 64})
    monkeypatch.setattr(
        data, "regular_file", lambda *args: pytest.fail("wrong source opened final labels")
    )
    with (
        closing(_index(tmp_path / "inputs.sqlite", plan.max_index_bytes)) as db,
        closing(_index(tmp_path / "actuals.sqlite", plan.max_index_bytes)) as outcomes,
    ):
        with pytest.raises(SnapshotError, match="final_source_recipe_mismatch"):
            data.index_role(db, outcomes, root, manifest, changed)


@pytest.mark.parametrize("mode", ["same-connection", "same-file", "attached"])
def test_actuals_cannot_share_or_attach_inference_database(evaluation_dataset, tmp_path, mode):
    root, manifest, plan, _, _ = evaluation_dataset
    input_path, actual_path = tmp_path / "inputs.sqlite", tmp_path / "actuals.sqlite"
    with (
        closing(_index(input_path, plan.max_index_bytes)) as db,
        closing(
            _index(input_path if mode == "same-file" else actual_path, plan.max_index_bytes)
        ) as outcomes,
    ):
        if mode == "attached":
            db.execute("ATTACH DATABASE ? AS labels", (str(actual_path),))
        with pytest.raises(SnapshotError, match="separate_actual_index_required"):
            data.index_role(db, db if mode == "same-connection" else outcomes, root, manifest, plan)
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='examples'").fetchone()


def test_wrong_manifest_scope_fails_before_role_io(evaluation_dataset, tmp_path, monkeypatch):
    root, manifest, plan, _, _ = evaluation_dataset
    opposite = evaluation_plan(
        "development_evaluation" if plan.role == "final_test" else "final_test"
    )
    monkeypatch.setattr(
        data, "regular_file", lambda *args: pytest.fail("wrong scope opened labels")
    )
    with (
        closing(_index(tmp_path / "inputs.sqlite", plan.max_index_bytes)) as db,
        closing(_index(tmp_path / "actuals.sqlite", plan.max_index_bytes)) as outcomes,
    ):
        with pytest.raises(SnapshotError, match="manifest_phase_role_mismatch"):
            data.index_role(db, outcomes, root, manifest, opposite)


def test_real_exposed_final_export_joins_complete_covariates_without_mocks(
    physical_parents, tmp_path
):
    snapshot, curated = physical_parents
    spec = native_spec(snapshot, curated)
    exported_plan = CampaignFinalExportPlan(
        source_recipe_sha256=canonical_sha256("exposed-control-not-project-final"),
        generation_operation_id="controlled-generation",
        origins=OriginWindow(start=date(2026, 7, 4), end=date(2026, 7, 5)),
        label_knowledge_cutoff=end_of_day(date(2026, 7, 31)),
        prior_exposure_end=date(2026, 6, 18),
    )
    runtime = runtime_pin()
    with _open_verified_source_parent(
        snapshot, curated, spec, limits=physical_limits(spec), runtime=runtime
    ) as replay:
        root = _build_final_forecast(replay, exported_plan.bind(spec), tmp_path / "exposed-final")
    manifest = verify_final_forecast(root)
    plan = evaluation_plan("final_test", source_recipe_sha256=exported_plan.source_recipe_sha256)
    with (
        closing(_index(tmp_path / "inputs.sqlite", plan.max_index_bytes)) as db,
        closing(_index(tmp_path / "actuals.sqlite", plan.max_index_bytes)) as outcomes,
    ):
        counts = data.index_role(db, outcomes, root, manifest, plan)
        windows = list(data.windows(db, plan))
        actuals = list(data.actuals(outcomes, plan))
        assert (
            sum(len(w.records) for w in windows)
            == len(actuals)
            == manifest.descriptor.population.row_count
        )
        assert counts["keys_sha256"] == manifest.descriptor.population.keys_sha256
        assert all(
            a.role == "final_test" and (a.actual is None if not a.eligible else a.actual >= 0)
            for a in actuals
        )
    assert not manifest.holdout_freshness_qualified and not manifest.stage_ready
