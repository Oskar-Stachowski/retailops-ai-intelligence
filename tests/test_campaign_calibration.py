"""Real journal/receipt guards around explicitly mocked fit and scoring workers."""

import hashlib
import sqlite3
from contextlib import closing

import pytest
from pydantic import ValidationError
from test_campaign_calibration_data import calibration_plan
from test_campaign_score import case as score_case
from test_campaign_score import run as run_score
from test_campaign_score_data import score_plan
from test_campaign_tune_data import diagnostic, prediction, tune_plan, values

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_calibration as runner
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign import campaign_score, campaign_tune
from retailops_ai.evaluation_campaign.campaign_calibration_data import fit_horizons
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.evaluation_campaign.campaign_score_metrics import RawMetrics
from retailops_ai.evaluation_campaign.campaign_tune_data import choose
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.source_snapshot.files import SnapshotError


def raw_phases(monkeypatch, value):
    root, _, _, plan, exported, fits = value
    rows = [prediction(i, values()).model_copy(update={"role": plan.role}) for i in range(100)]
    digest = hashlib.sha256(b"".join(membership_key(row) + b"\n" for row in rows)).hexdigest()
    population = dict(
        rows=100,
        eligible_rows=100,
        keys_sha256=digest,
        eligible_keys_sha256=digest,
        role_population_sha256=canonical_sha256(plan.role),
    )

    def parent(*args, **kwargs):
        assert journal.inspect(root).events[-1].kind == "reserved"

    monkeypatch.setattr(campaign_score, "verify_campaign_forecast_bundle", parent)

    def monitor(command, **kwargs):
        phase, folder = command[-2], kwargs["root"]
        assert journal.inspect(root).events[-1].kind == "reserved"
        if phase == "predict":
            bundle = folder / "bundle"
            bundle.mkdir(mode=0o700)
            write(bundle / "plan.json", plan.model_dump(mode="json"))
            write(
                bundle / "parents.json",
                {
                    "export_receipt_sha256": exported.content_sha256(),
                    "fit_receipt_sha256": {f: fit.content_sha256() for f, fit in fits.items()},
                    "model_artifact_sha256": {
                        f: fit.model_artifact_sha256 for f, fit in fits.items()
                    },
                },
            )
            metrics = RawMetrics()
            for row in rows:
                metrics.add(row, 10)
            write(bundle / "metrics.json", metrics.result())
            (bundle / "predictions.jsonl").write_bytes(
                b"".join(canonical_bytes(row.model_dump(mode="json")) + b"\n" for row in rows)
            )
        write(
            folder / (phase + ".json"),
            population | {"worker_peak_rss_bytes": 12345, "all_models_share_all_role_keys": True},
        )
        return dict(status="passed", reason=None, sampled_tree_peak_rss_bytes=12345)

    monkeypatch.setattr(campaign_score, "monitor", monitor)


def case(tmp_path, monkeypatch, *, scores_follow_tune=True):
    saved = {}

    def finalize(document):
        raw = document["operations"][-1]
        tp = tune_plan(
            (raw["operation_id"],),
            source_recipe_sha256=raw["source_recipe_sha256"],
            export_operation_id="development-42-read",
            campaign_selection_policy_sha256=document["selection_policy_sha256"],
            forecast_quality_policy_sha256=document["use_case_quality_policy_sha256"]["forecast"],
        )
        sp = score_plan(
            role="calibration",
            source_recipe_sha256=tp.source_recipe_sha256,
            export_operation_id=tp.export_operation_id,
            fit_operation_ids={
                f: "development-fit" if f == "rf" else "development-fit-" + f
                for f in ("rf", "hgb", "tensorflow")
            },
        )
        cp = calibration_plan(
            ("development-calibration-score",),
            source_recipe_sha256=tp.source_recipe_sha256,
            export_operation_id=tp.export_operation_id,
            tune_operation_id="development-select",
            forecast_quality_policy_sha256=tp.forecast_quality_policy_sha256,
        )
        for op, action, role, plan, prerequisites in (
            (
                "development-select",
                "model_score",
                "tune",
                tp,
                [tp.export_operation_id, *tp.score_operation_ids],
            ),
            (
                "development-calibration-score",
                "model_score",
                "calibration",
                sp,
                [sp.export_operation_id, *sp.fit_operation_ids.values(), "development-select"],
            ),
            (
                "development-calibrate",
                "calibrator_fit",
                "calibration",
                cp,
                [cp.export_operation_id, cp.tune_operation_id, *cp.score_operation_ids],
            ),
        ):
            document["operations"].append(
                dict(
                    operation_id=op,
                    phase="development",
                    action=action,
                    use_case="forecast",
                    role=role,
                    source_recipe_sha256=tp.source_recipe_sha256,
                    execution_recipe_sha256=plan.content_sha256(),
                    prerequisites=prerequisites,
                    maximum_attempts=1,
                )
            )
        if not scores_follow_tune:
            document["operations"][-2]["prerequisites"].remove("development-select")
        document["maximum_new_attempts"] = sum(
            o.get("maximum_attempts", 1) for o in document["operations"]
        )
        document["maximum_new_fit_attempts"] = sum(
            o.get("maximum_attempts", 1)
            for o in document["operations"]
            if o["action"] in ("model_fit", "calibrator_fit")
        )
        saved.update(tune=tp, score=sp, calibration=cp)

    value = score_case(tmp_path, monkeypatch, finalize_document=finalize)
    raw_phases(monkeypatch, value)
    _, tune_score = run_score(value)
    root, dataset, output, _, exported, fits = value
    tp = saved["tune"]

    def tune_monitor(command, **kwargs):
        folder = kwargs["root"]
        trial, _ = diagnostic(tune_score.operation_id)
        trial.update({field: getattr(tune_score, field) for field in runner.POPULATION})
        selection = choose([trial], {tune_score.operation_id: tune_score}, tp)
        bundle = folder / "bundle"
        bundle.mkdir(mode=0o700)
        write(bundle / "plan.json", tp.model_dump(mode="json"))
        write(
            bundle / "parents.json",
            {
                "export_receipt_sha256": exported.content_sha256(),
                "scores": {tune_score.operation_id: tune_score.model_dump(mode="json")},
            },
        )
        write(bundle / "metrics.json", {"trials": [trial]})
        write(bundle / "selection.json", selection.model_dump(mode="json"))
        write(
            folder / "select.json",
            {field: trial[field] for field in (*runner.POPULATION, "baseline_predictions_sha256")}
            | dict(
                selection=selection.model_dump(mode="json"),
                worker_peak_rss_bytes=12345,
                trial_count=1,
                full_tune_label_passes=1,
                calibration_label_passes=0,
                independent_or_final_label_passes=0,
            ),
        )
        return dict(status="passed", reason=None, sampled_tree_peak_rss_bytes=12345)

    monkeypatch.setattr(campaign_tune, "monitor", tune_monitor)
    tune_bundle, tune_receipt = campaign_tune.select_campaign_forecast(
        dataset,
        {tune_score.operation_id: output / tune_score.reservation_id / "bundle"},
        output / "unused-python",
        output,
        journal=root,
        operation_id="development-select",
        plan=tp,
        exported=exported,
        scores={tune_score.operation_id: tune_score},
    )
    sp = saved["score"]
    calib_value = root, dataset, output, sp, exported, fits
    raw_phases(monkeypatch, calib_value)
    calib_bundle, calib_score = run_score(calib_value, operation="development-calibration-score")
    return (
        root,
        dataset,
        output,
        saved["calibration"],
        exported,
        tune_bundle,
        tune_receipt,
        calib_bundle,
        calib_score,
    )


def run(value, *, plan=None, score=None, tune=None, operation="development-calibrate"):
    (
        root,
        dataset,
        output,
        original,
        exported,
        tune_bundle,
        tune_receipt,
        calib_bundle,
        calib_score,
    ) = value
    score = score or calib_score
    return runner.fit_campaign_forecast_calibration(
        dataset,
        tune_bundle,
        {score.operation_id: calib_bundle},
        output / "unused-python",
        output,
        journal=root,
        operation_id=operation,
        plan=plan or original,
        exported=exported,
        tune=tune or tune_receipt,
        scores={score.operation_id: score},
    )


def fake_worker(monkeypatch, value, *, failed=False, corrupt=False, peak=12345):
    root, _, _, plan, exported, _, tune, _, score = value
    calls = []
    original = runner.verify_campaign_forecast_selection

    def verify(*args, **kwargs):
        event = journal.inspect(root).events[-1]
        assert event.kind == "reserved" and event.operation_id == "development-calibrate"
        calls.append("tune-parent")
        original(*args, **kwargs)

    monkeypatch.setattr(runner, "verify_campaign_forecast_selection", verify)

    def monitor(command, **kwargs):
        calls.append("worker")
        assert journal.inspect(root).events[-1].kind == "reserved"
        if failed:
            return dict(
                status="failed", reason="controlled_worker_exit", sampled_tree_peak_rss_bytes=12345
            )
        folder = kwargs["root"]
        request = __import__("json").loads((folder / "request.json").read_bytes())
        with closing(sqlite3.connect(folder / "controlled-residuals.sqlite")) as db:
            db.execute("CREATE TABLE residuals(horizon INTEGER,error REAL,ordinal INTEGER)")
            db.executemany("INSERT INTO residuals VALUES (1,0,?)", ((i,) for i in range(100)))
            counts = {h: (100, 100) if h == 1 else (0, 0) for h in range(1, 15)}
            calibrated = fit_horizons(db, counts, tune.selection, score.operation_id, plan)
        population = {field: getattr(score, field) for field in runner.POPULATION} | dict(
            selected_median_residuals_sha256="a" * 64,
            selected_prediction_file_sha256=score.artifact_files["predictions.jsonl"],
            full_calibration_label_passes=1,
            tune_label_passes=1 if corrupt else 0,
            independent_or_final_label_passes=0,
            architecture_reselected=False,
        )
        bundle = folder / "bundle"
        bundle.mkdir(mode=0o700)
        write(bundle / "plan.json", plan.model_dump(mode="json"))
        write(
            bundle / "parents.json",
            {key: request[key] for key in ("exported", "tune", "tune_scores", "scores")},
        )
        write(bundle / "population.json", population)
        write(bundle / "calibration.json", calibrated.model_dump(mode="json"))
        write(
            folder / "calibrate.json",
            population
            | dict(calibration=calibrated.model_dump(mode="json"), worker_peak_rss_bytes=peak),
        )
        return dict(status="passed", reason=None, sampled_tree_peak_rss_bytes=12345)

    monkeypatch.setattr(runner, "monitor", monitor)
    return calls


def test_reservation_precedes_reads_and_private_receipt_precedes_completion(tmp_path, monkeypatch):
    value = case(tmp_path, monkeypatch)
    calls = fake_worker(monkeypatch, value)
    original_finish = journal.finish

    def finish(root, reservation, **kwargs):
        path = root / "receipts" / (reservation + ".json")
        assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
        assert journal.inspect(root).events[-1].kind == "reserved"
        return original_finish(root, reservation, **kwargs)

    monkeypatch.setattr(journal, "finish", finish)
    bundle, receipt = run(value)
    assert calls == ["tune-parent", "worker"]
    assert receipt.calibration.status == "not_ready" and not receipt.stage_ready
    completed = journal.inspect(value[0]).events[-1]
    assert completed.result == "completed" and completed.evidence_sha256 == receipt.content_sha256()
    assert completed.cost.peak_process_tree_rss_bytes == 12345 and completed.cost.artifact_bytes > 0
    runner.verify_campaign_forecast_calibration(bundle, journal=value[0], receipt=receipt)
    with pytest.raises(ValidationError, match="budget_exhausted"):
        run(value)
    (bundle / "calibration.json").write_bytes(b"{}\n")
    with pytest.raises(SnapshotError, match="checksum"):
        runner.verify_campaign_forecast_calibration(bundle, journal=value[0], receipt=receipt)


def test_calibration_scores_without_a_prerequisite_tune_freeze_are_refused(tmp_path, monkeypatch):
    value = case(tmp_path, monkeypatch, scores_follow_tune=False)
    calls = fake_worker(monkeypatch, value)
    with pytest.raises(SnapshotError, match="must_follow_tune_freeze"):
        run(value)
    assert calls == [] and journal.inspect(value[0]).events[-1].result == "failed"


@pytest.mark.parametrize("failure", ["exit", "role", "peak"])
def test_failed_worker_or_forbidden_exposure_preserves_attempt_and_peak(
    tmp_path, monkeypatch, failure
):
    value = case(tmp_path, monkeypatch)
    peak = value[3].resources.tree_rss_bytes + 1 if failure == "peak" else 12345
    fake_worker(monkeypatch, value, failed=failure == "exit", corrupt=failure == "role", peak=peak)
    with pytest.raises(SnapshotError, match="worker_failed|scope_or_peak"):
        run(value)
    completed = journal.inspect(value[0]).events[-1]
    assert completed.result == "failed" and completed.cost.peak_process_tree_rss_bytes == peak
    assert not (value[0] / "receipts" / (str(completed.reservation_id) + ".json")).exists()


@pytest.mark.parametrize("mutation", ["plan", "source", "role", "model", "population"])
def test_parent_or_plan_failure_is_charged_before_worker(tmp_path, monkeypatch, mutation):
    value = case(tmp_path, monkeypatch)
    calls = fake_worker(monkeypatch, value)
    plan, score = value[3], value[8]
    if mutation == "plan":
        plan = plan.model_copy(update={"max_rows": 1})
    elif mutation == "source":
        score = score.model_copy(
            update={"plan": score.plan.model_copy(update={"source_recipe_sha256": "f" * 64})}
        )
    elif mutation == "role":
        score = score.model_copy(update={"plan": score.plan.model_copy(update={"role": "tune"})})
    elif mutation == "model":
        score = score.model_copy(
            update={"model_artifact_sha256": score.model_artifact_sha256 | {"hgb": "f" * 64}}
        )
    else:
        score = score.model_copy(update={"keys_sha256": "f" * 64})
    with pytest.raises(SnapshotError):
        run(value, plan=plan, score=score)
    assert "worker" not in calls and journal.inspect(value[0]).events[-1].result == "failed"


@pytest.mark.parametrize("operation", ["development-fit", "final-42-score-forecast", "unknown"])
def test_unrelated_operations_cannot_consume_the_calibration_grant(
    tmp_path, monkeypatch, operation
):
    value = case(tmp_path, monkeypatch)
    before = (value[0] / "journal.json").read_bytes()
    with pytest.raises(SnapshotError, match="requires_development"):
        run(value, operation=operation)
    assert (value[0] / "journal.json").read_bytes() == before
