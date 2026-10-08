"""Native exposed Source1.2 role joins; controlled forecasts, no Project fits/final access."""

import hashlib
import json
import shutil
import zlib
from contextlib import closing

import pytest
from pydantic import ValidationError
from test_campaign_context_bundle import native_case as native_case
from test_campaign_evaluation_configuration import configuration
from test_campaign_evaluation_data import evaluation_plan
from test_campaign_segment_metrics import collector, contexts
from test_campaign_segment_metrics import row as prediction

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_evaluation_worker as worker
from retailops_ai.evaluation_campaign.campaign_generation_worker import read
from retailops_ai.evaluation_campaign.campaign_raw_context import (
    context_record,
    validate_raw_context_metrics,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastKeyContext,
    CampaignForecastSegmentCensus,
)
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_forecast import _index
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError, file_hash


@pytest.fixture
def native_trial(native_case, tmp_path):
    source_root, source_request, _, receipt = native_case
    frozen = configuration().model_copy(update={"runtime_code_sha256": receipt.runtime_code_sha256})
    plan = evaluation_plan(
        "final_test",
        source_recipe_sha256=receipt.recipe.source_recipe_sha256,
        export_operation_id=receipt.recipe.export_operation_id,
        frozen_configuration_sha256=frozen.content_sha256(),
        segment_policy_sha256=receipt.scope.segment_policy_sha256,
        worker_environment_lock_sha256=frozen.worker_environment_lock_sha256,
    )
    prepare = tmp_path / "prepare"
    prepare.mkdir(mode=0o700)
    population = worker.prepare(prepare, source_request, plan)
    contexts = [
        CampaignForecastKeyContext.model_validate_json(raw)
        for raw in (source_root / "bundle/contexts.jsonl").read_bytes().splitlines()
    ]
    record = {
        "receipt": receipt.model_dump(mode="json"),
        "census": read(source_root / "bundle/census.json"),
    }
    return tmp_path, plan, frozen, population, contexts, record, source_root / "bundle"


def trial_request(case, index):
    root, plan, frozen, population, contexts, record, bundle = case
    trial = frozen.trials[index]
    target = root / f"trial-{index}"
    target.mkdir(mode=0o700)
    digest, baseline = hashlib.sha256(), hashlib.sha256()
    with closing(_index(target / "predictions.sqlite", plan.max_index_bytes)) as db:
        db.execute("CREATE TABLE predictions(key BLOB PRIMARY KEY,body BLOB)")
        for ctx in contexts:
            row = prediction(ctx, trial=trial.tune_score_operation_id)
            # Baselines are identical; learned raw forecasts differ for each
            # retained trial. No fitted ML model is constructed or claimed.
            values = tuple(
                value
                if model < 3 or not ctx.eligible
                else FunctionalForecast(
                    mean=value.mean + index,
                    median=None if value.median is None else value.median + index,
                    interval=value.interval,
                )
                for model, value in enumerate(row.values)
            )
            row = row.model_copy(update={"values": values})
            raw = canonical_bytes(row.model_dump(mode="json"))
            db.execute(
                "INSERT INTO predictions VALUES(?,?)", (membership_key(row), zlib.compress(raw))
            )
            digest.update(raw + b"\n")
            baseline.update(
                canonical_bytes(
                    [
                        row.model_dump(mode="json", include=set(ForecastKey.model_fields)),
                        [v.model_dump(mode="json") for v in row.values[:3]],
                    ]
                )
                + b"\n"
            )
        db.commit()
    request = {
        "plan": plan.model_dump(mode="json"),
        "runtime": {"code_sha256": frozen.runtime_code_sha256},
        "configuration": frozen.model_dump(mode="json"),
        "trial": trial.model_dump(mode="json"),
        "population": population,
        "inputs": str(root / "prepare/inputs.sqlite"),
        "actuals": str(root / "prepare/actuals.sqlite"),
        "projection": str(root / "projection.sqlite"),
        "metrics_output": str(root / f"metrics-{index}.json"),
        "predicted": {
            "prediction_trace_sha256": digest.hexdigest(),
            "baseline_trace_sha256": baseline.hexdigest(),
            "prediction_index_sha256": file_hash(target, "predictions.sqlite")[1],
        },
        "raw_context": record,
        "raw_context_bundle": str(bundle),
    }
    return target, request


def test_real_full_source_role_consumes_every_raw_trial_model_and_declared_group(native_trial):
    root, plan, frozen, population, _, record, _ = native_trial
    _, census = context_record(record, plan, population)
    reports = []
    for index, trial in enumerate(frozen.trials):
        target, request = trial_request(native_trial, index)
        result = worker.consume(target, request, plan)
        assert result["raw_critical_segments_complete"] is True
        assert result["actual_index_passes"] == 1
        metrics = read(root / f"metrics-{index}.json")
        report = metrics["raw_critical_segments"]
        validate_raw_context_metrics(report, census, plan, trial.tune_score_operation_id)
        assert report["rows"] == population["rows"] and report["rows"] > 0
        assert report["eligible_rows"] == population["eligible_rows"]
        assert report["keys_sha256"] == population["keys_sha256"]
        assert len(report["segments"]) == len(census.populations)
        assert all(set(group["models"]) == set(MODELS) for group in report["segments"])
        assert not report["quality_qualified"] and not report["stage_ready"]
        assert not metrics["quality_qualified"] and not metrics["stage_ready"]
        reports.append(report)
    assert len(reports) == len(frozen.trials) == 2
    assert (
        reports[0]["trial_tune_score_operation_id"] != reports[1]["trial_tune_score_operation_id"]
    )
    if population["eligible_rows"]:
        assert reports[0]["segments"] != reports[1]["segments"]
    else:
        # The short exposed Source fixture cannot establish sufficient history
        # plus mature full-horizon labels. Keep every exclusion and undefined
        # metric; it is not positive scientific qualification.
        for report in reports:
            assert all(
                metrics["mean"]["mse"] is None and metrics["median"]["wape"] is None
                for group in report["segments"]
                for metrics in group["models"].values()
            )


@pytest.mark.parametrize("attack", ["drop", "duplicate", "reverse", "example", "scope"])
def test_resealed_context_cannot_change_the_full_prediction_actual_join(
    native_trial, tmp_path, attack
):
    target, request = trial_request(native_trial, 0)
    bundle = tmp_path / "tampered-context"
    shutil.copytree(request["raw_context_bundle"], bundle)
    records = (bundle / "contexts.jsonl").read_bytes().splitlines(keepends=True)
    if attack == "drop":
        records.pop()
    elif attack == "duplicate":
        records.append(records[-1])
    elif attack == "reverse":
        records.reverse()
    else:
        row = json.loads(records[0])
        row["example_sha256" if attack == "example" else "context_scope_sha256"] = "0" * 64
        records[0] = canonical_bytes(row) + b"\n"
    (bundle / "contexts.jsonl").write_bytes(b"".join(records))
    metadata = json.loads(canonical_bytes(request["raw_context"]))
    hashes = metadata["receipt"]["artifact_files"]
    hashes["contexts.jsonl"] = file_hash(bundle, "contexts.jsonl")[1]
    metadata["receipt"]["artifact_sha256"] = canonical_sha256(hashes)
    metadata["receipt"]["artifact_bytes"] = sum((bundle / name).stat().st_size for name in hashes)
    request |= {"raw_context": metadata, "raw_context_bundle": str(bundle)}
    with pytest.raises((SnapshotError, ValidationError)):
        worker.consume(target, request, native_trial[1])
    assert not (tmp_path / "metrics-0.json").exists()


@pytest.mark.parametrize("attack", ["rows", "keys", "source", "policy", "role"])
def test_other_population_or_frozen_policy_fails_before_context_open(native_trial, attack):
    _, plan, _, population, _, record, _ = native_trial
    metadata = json.loads(canonical_bytes(record))
    if attack in ("rows", "keys"):
        population = dict(population)
        population["rows" if attack == "rows" else "keys_sha256"] = (
            1 if attack == "rows" else "0" * 64
        )
    else:
        field = {
            "source": "source_recipe_sha256",
            "policy": "segment_policy_sha256",
            "role": "role",
        }[attack]
        plan = plan.model_copy(
            update={field: "development_evaluation" if attack == "role" else "0" * 64}
        )
    with pytest.raises(SnapshotError, match="full_population_or_policy_mismatch"):
        context_record(metadata, plan, population)


@pytest.mark.parametrize("attack", ["group", "model", "population", "qualify"])
def test_report_verification_rejects_missing_groups_models_or_changed_scope(native_trial, attack):
    target, request = trial_request(native_trial, 0)
    worker.consume(target, request, native_trial[1])
    report = read(native_trial[0] / "metrics-0.json")["raw_critical_segments"]
    census = CampaignForecastSegmentCensus.model_validate_json(
        canonical_bytes(request["raw_context"]["census"])
    )
    if attack == "group":
        report["segments"].pop()
    elif attack == "model":
        report["segments"][0]["models"].pop(MODELS[-1])
    elif attack == "population":
        report["segments"][0]["eligible_rows"] += 1
    else:
        report["quality_qualified"] = True
    with pytest.raises(SnapshotError):
        validate_raw_context_metrics(
            report, census, native_trial[1], request["trial"]["tune_score_operation_id"]
        )


@pytest.mark.parametrize(
    "attack",
    [None, "missing-metric", "nonfinite", "false-complete", "undefined-zero", "boolean-count"],
)
def test_defined_equations_and_undefined_empty_groups_have_valid_metric_shape(attack):
    rows = contexts()
    stream = collector(rows)
    for ctx, actual in zip(rows, (0, 4, None), strict=True):
        stream.add(prediction(ctx), actual, ctx)
    report = stream.finish()
    census = stream.census
    plan = evaluation_plan(
        source_recipe_sha256=census.scope.source_recipe_sha256,
        segment_policy_sha256=census.scope.segment_policy_sha256,
    )
    metric = next(g for g in report["segments"] if g["dimension"] == "global")["models"][MODELS[0]]
    empty = next(
        g for g in report["segments"] if g["dimension"] == "category" and g["value"] == "c2"
    )["models"][MODELS[0]]
    if attack is None:
        validate_raw_context_metrics(report, census, plan, "trial1")
        assert metric["mean"]["mse"] == 4.0
        assert empty["mean"]["mse"] is None
        return
    if attack == "missing-metric":
        metric["mean"].pop("mse")
    elif attack == "nonfinite":
        metric["mean"]["mse"] = float("inf")
    elif attack == "false-complete":
        empty["mean"]["complete"] = True
    elif attack == "undefined-zero":
        empty["mean"]["mse"] = 0.0
    else:
        empty["rows"] = False
    with pytest.raises(SnapshotError):
        validate_raw_context_metrics(report, census, plan, "trial1")
