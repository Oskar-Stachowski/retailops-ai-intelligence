"""Run evidence preserves failed forecasts, exact replay and all retained payloads."""

import errno
import os
from copy import deepcopy
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from test_forecast_functional_v12_campaign import archived_campaign as archived_campaign
from test_forecast_functional_v12_campaign import score

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting import functional_v12_run as run
from retailops_ai.forecasting.functional_v12_cohort import observation_from_compact
from retailops_ai.forecasting.functional_v12_quality import (
    CampaignObservation,
    StreamingCampaignScorer,
)
from retailops_ai.forecasting.functional_v12_recipe import PreparedV12Predictor
from retailops_ai.forecasting.quality_v2_contract import (
    CentralInterval,
    FunctionalForecast,
    ProtocolObservation,
    QualityPolicyV2,
)
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, read_json


@pytest.fixture
def scored(archived_campaign):
    campaign = score(archived_campaign)
    replay = score(archived_campaign, replay=campaign)
    return archived_campaign, campaign, replay


def test_failed_campaign_exports_all_bytes_and_verifies_after_original_name_removed(
    scored, tmp_path
):
    prepared, campaign, replay = scored
    target = run.export_run(campaign, replay, prepared["checkpoints"], tmp_path / "runs", "a" * 40)
    manifest = run.verify_run(target)
    assert manifest["descriptor"]["forecast_model_status"] == "not_ready"
    original = prepared["checkpoints"][0] / "payload.tar.gz"
    retained = target / "checkpoints/seed-1/payload.tar.gz"
    assert original.stat().st_ino == retained.stat().st_ino
    assert read_json(target, "storage_receipt.json")["hardlink"] > 0
    assert read_json(target, "handoff.json")["independent_replay"] == "passed"
    original.unlink()
    assert retained.is_file()
    assert run.verify_run(target) == manifest
    assert (
        read_json(target / "campaign", "metrics.json")["failed_reasons"][
            "absolute_normalized_mean_bias_exceeded"
        ]
        > 0
    )


def test_model_reports_bind_saved_recipe_cutoffs_dual_targets_and_import_limits(scored, tmp_path):
    prepared, campaign, replay = scored
    target = run.export_run(campaign, replay, prepared["checkpoints"], tmp_path / "runs", "a" * 40)
    manifest = run.verify_run(target)
    card = read_json(target, "model_card.json")
    signature = read_json(target, "signature.json")
    example = read_json(target, "input_example.json")
    handoff = read_json(target, "handoff.json")
    for name in ("model_card.json", "signature.json", "input_example.json"):
        assert manifest["descriptor"]["files"][name]["sha256"] == file_hash(target, name)[1]
    assert card["method_policy"] == prepared["freeze"]["descriptor"]["method_policy"]
    assert card["failed_reasons"] and card["forecast_model_status"] == "not_ready"
    assert set(card["cohort_lineage"]) == {"seed-1", "seed-2"}
    assert card["resources"]["inference_latency"] is None
    assert card["times"]["training_started_at"] is None
    assert handoff["ai05_import"]["legacy_single_point_import_compatible"] is False
    assert handoff["deployment"] == "not_promoted"
    assert set(signature["outputs"]["candidate"]) == {"median", "mean", "interval"}
    assert example["data_rows_read"] == 0 and example["prediction_executed"] is False
    assert example["input"]["row"]["actual"] is None
    assert example["input"]["row"]["label_available_at"] is None
    assert example["selection_cutoff"] < example["input"]["row"]["origin"]
    recipe = read_json(target, example["bound_recipe"]["path"])
    assert recipe["recipe_id"] == example["input"]["recipe_id"]
    validator = Draft202012Validator(signature["input_schema"], format_checker=FormatChecker())
    validator.validate(example["input"])
    candidate, baseline, metadata = PreparedV12Predictor(recipe).predict(
        observation_from_compact(example["input"]["row"], example["input"]["cohort_id"])
    )
    assert candidate.median == baseline.median and candidate.interval == baseline.interval
    assert metadata["recipe_id"] == example["input"]["recipe_id"]
    for prediction in (candidate, baseline):
        Draft202012Validator(signature["output_schema"]).validate(
            prediction.model_dump(mode="json")
        )
    for field, value in (("actual", 1), ("label_available_at", "2026-01-01T00:00:00Z")):
        changed = deepcopy(example["input"])
        changed["row"][field] = value
        with pytest.raises(ValidationError):
            validator.validate(changed)
    # Mean and median remain separate; the mean can lie above the central interval.
    output = FunctionalForecast(median=0, mean=2, interval=CentralInterval(lower=0, upper=0))
    Draft202012Validator(signature["output_schema"]).validate(output.model_dump(mode="json"))


@pytest.mark.parametrize("name", ["model_card.json", "signature.json", "input_example.json"])
def test_rehashed_model_reports_cannot_change_the_retained_contract(scored, tmp_path, name):
    prepared, campaign, replay = scored
    target = run.export_run(campaign, replay, prepared["checkpoints"], tmp_path / "runs", "a" * 40)
    changed = read_json(target, name)
    if name == "model_card.json":
        changed["method_policy"]["zero_estimation"] = "train_validation_pooled"
    elif name == "signature.json":
        changed["outputs"]["candidate"]["mean"] = "same as median"
    else:
        changed["input"]["row"]["actual"] = 123
    (target / name).write_bytes(canonical_bytes(changed) + b"\n")
    manifest = read_json(target, "run_manifest.json")
    size, digest = file_hash(target, name)
    manifest["descriptor"]["files"][name] = {"size_bytes": size, "sha256": digest}
    manifest["descriptor"]["bytes"] = sum(
        ref["size_bytes"] for ref in manifest["descriptor"]["files"].values()
    )
    manifest["run_id"] = "functional-v12-run-sha256-" + canonical_sha256(manifest["descriptor"])
    (target / "run_manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    with pytest.raises(SnapshotError, match="model_report_binding"):
        run.verify_run(target)


def test_model_card_reads_saved_metadata_only_and_rejects_wrong_recipe_cutoff(scored, monkeypatch):
    _, campaign, _ = scored
    manifest = read_json(campaign, "campaign_manifest.json")
    original = run.read_json
    opened = []

    def metadata_only(root, name):
        assert root == campaign and (name == "fitted_recipes.json" or name.startswith("recipes/"))
        opened.append(name)
        return original(root, name)

    monkeypatch.setattr(run, "read_json", metadata_only)
    reports = run._model_reports(campaign, manifest, "not_ready")
    assert len(opened) == 7
    assert (
        reports["model_card.json"]["feature_boundary"]["observed_labels_used_by_predictor"] is False
    )

    def wrong_cutoff(root, name):
        value = metadata_only(root, name)
        if name.startswith("recipes/"):
            value["selection_cutoff"] = "2099-01-01T00:00:00Z"
        return value

    monkeypatch.setattr(run, "read_json", wrong_cutoff)
    with pytest.raises(SnapshotError, match="card_recipe_binding"):
        run._model_reports(campaign, manifest, "not_ready")


def test_export_never_overwrites_existing_run_and_tampering_cannot_pass(scored, tmp_path):
    prepared, campaign, replay = scored
    output = tmp_path / "runs"
    target = run.export_run(campaign, replay, prepared["checkpoints"], output, "a" * 40)
    with pytest.raises(FileExistsError):
        run.export_run(campaign, replay, prepared["checkpoints"], output, "a" * 40)
    with (target / "checkpoints/seed-1/payload.tar.gz").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(SnapshotError, match="file_checksum"):
        run.verify_run(target)


def test_missing_checkpoint_and_mismatched_replay_cannot_qualify(scored, tmp_path):
    prepared, campaign, replay = scored
    with pytest.raises(SnapshotError, match="complete_inventory"):
        run.export_run(
            campaign, replay, prepared["checkpoints"][:1], tmp_path / "missing", "a" * 40
        )
    proof = read_json(replay, "replay_receipt.json")
    proof["descriptor"]["refit_calls"] = 1
    proof["replay_id"] = "functional-v12-replay-sha256-" + canonical_sha256(proof["descriptor"])
    (replay / "replay_receipt.json").write_bytes(canonical_bytes(proof) + b"\n")
    with pytest.raises(SnapshotError, match="independent_replay_binding"):
        run.export_run(campaign, replay, prepared["checkpoints"], tmp_path / "bad-replay", "a" * 40)


def test_saved_metric_status_cannot_hide_real_bias_or_missing_segment(scored):
    prepared, campaign, _ = scored
    metrics = read_json(campaign, "metrics.json")
    ids = ["seed-1", "seed-2"]
    assert run.validate_metrics(metrics, prepared["freeze"], ids) == "not_ready"
    changed = deepcopy(metrics)
    segment = next(s for s in changed["segments"] if s["failed_reasons"])
    segment["failed_reasons"] = []
    segment["has_measurable_failures"] = False
    segment["status"] = "passed"
    with pytest.raises(SnapshotError, match="quality_gate_contract"):
        run.validate_metrics(changed, prepared["freeze"], ids)
    missing = deepcopy(metrics)
    missing["segments"].pop()
    with pytest.raises(SnapshotError, match="overall_contract"):
        run.validate_metrics(missing, prepared["freeze"], ids)


@pytest.mark.parametrize("has_interval", [True, False])
def test_zero_forecasts_with_complete_or_missing_intervals_keep_the_report_contract(
    tmp_path, has_interval
):
    policy = QualityPolicyV2()
    dimensions = {
        "category": ["one"],
        "channel": ["store"],
        "volume": ["high", "low", "medium", "zero"],
    }
    prediction = FunctionalForecast(
        median=0,
        mean=0,
        interval=CentralInterval(lower=0, upper=0) if has_interval else None,
    )
    with StreamingCampaignScorer(
        cohorts=["seed-1"],
        folds=["fold-1"],
        dimensions=dimensions,
        max_rows=4000,
        max_index_bytes=1024**2,
        temporary_directory=tmp_path,
    ) as scorer:
        for role in ("validation", "development_holdout"):
            for horizon in range(1, 15):
                for volume in dimensions["volume"]:
                    for index in range(30):
                        scorer.add(
                            CampaignObservation(
                                cohort_id="seed-1",
                                fold="fold-1",
                                role=role,
                                horizon=horizon,
                                category="one",
                                channel="store",
                                volume=volume,
                                observation=ProtocolObservation(
                                    key=f"{role}:{horizon}:{volume}:{index}",
                                    actual=0,
                                    candidate=prediction,
                                    baseline=prediction,
                                ),
                                retained_median_baseline=True,
                                candidate_calibration_rows=50,
                                baseline_calibration_rows=50,
                            )
                        )
        metrics = scorer.finalize()
    freeze = {
        "descriptor": {
            "quality_policy": policy.model_dump(mode="json"),
            "split_policy": {"folds": [{"name": "fold-1"}]},
            "required_dimensions": dimensions,
        }
    }
    expected = "passed" if has_interval else "not_ready"
    assert metrics["status"] == expected
    assert run.validate_metrics(metrics, freeze, ["seed-1"]) == expected
    changed = deepcopy(metrics)
    changed["processed_rows"] = 0
    with pytest.raises(SnapshotError, match="processed_rows_binding"):
        run.validate_metrics(changed, freeze, ["seed-1"])
    invalid = deepcopy(metrics)
    invalid["segments"][0]["calibration_evidence"]["candidate"]["minimum_rows"] = 49
    with pytest.raises(SnapshotError, match="quality_gate_contract"):
        run.validate_metrics(invalid, freeze, ["seed-1"])


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("counts", "cohort_result_counts_binding"),
        ("exposure", "exposure_binding"),
        ("duplicate_source", "nonindependent_or_previously_used"),
        ("previous_snapshot", "nonindependent_or_previously_used"),
        ("global", "metric_prepared_counts_binding"),
    ],
)
def test_structural_inventory_bindings_reject_self_consistent_metadata_changes(
    scored, mutation, error
):
    _, campaign, _ = scored
    desc = read_json(campaign, "campaign_manifest.json")["descriptor"]
    metrics = read_json(campaign, "metrics.json")
    run._validate_complete_results(campaign, desc, metrics)
    results = read_json(campaign, "cohort_results.json")
    if mutation == "counts":
        first = next(iter(results["seed-1"]["counts"].values()))
        first["total"] += 1
    elif mutation == "exposure":
        receipt = results["seed-1"]["exposure"]
        receipt["descriptor"]["fitted_recipes_sha256"] = "f" * 64
        # A valid receipt checksum cannot substitute different fitted parameters.
        receipt["exposure_id"] = "cohort-exposure-sha256-" + canonical_sha256(receipt["descriptor"])
    elif mutation == "duplicate_source":
        desc["cohorts"][1]["descriptor"]["source_dataset_id"] = desc["cohorts"][0]["descriptor"][
            "source_dataset_id"
        ]
    elif mutation == "previous_snapshot":
        desc["freeze"]["descriptor"]["previously_used_snapshot_ids"].append(
            desc["cohorts"][0]["descriptor"]["snapshot_id"]
        )
    elif mutation == "global":
        # The metric report remains internally consistent, but omits all input rows.
        for segment in metrics["segments"]:
            segment["total_rows"] = segment["eligible_rows"] = 0
        metrics["processed_rows"] = 0
    (campaign / "cohort_results.json").write_bytes(canonical_bytes(results) + b"\n")
    with pytest.raises(SnapshotError, match=error):
        run._validate_complete_results(campaign, desc, metrics)


def test_cross_device_copy_requires_actual_space_and_preserves_exact_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload").write_bytes(b"retained bytes")
    size, digest = file_hash(source, "payload")
    receipt = {"size_bytes": size, "sha256": digest}

    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, "different device")

    monkeypatch.setattr(os, "link", cross_device)
    monkeypatch.setattr(
        run.shutil, "disk_usage", lambda _: SimpleNamespace(free=run.MIN_FREE_BYTES)
    )
    target = tmp_path / "target" / "payload"
    with pytest.raises(SnapshotError, match="cross_device_copy_space_budget"):
        run._clone(source, "payload", target, receipt, run.MIN_FREE_BYTES)
    assert not target.exists()
    monkeypatch.setattr(
        run.shutil, "disk_usage", lambda _: SimpleNamespace(free=run.MIN_FREE_BYTES + size)
    )
    assert (
        run._clone(source, "payload", target, receipt, run.MIN_FREE_BYTES)
        == "copy_after_cross_device_preflight"
    )
    assert file_hash(target.parent, target.name) == (size, digest)
    assert target.stat().st_ino != (source / "payload").stat().st_ino


def test_symlinks_are_never_cloned(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (tmp_path / "outside").write_bytes(b"outside")
    (source / "payload").symlink_to(tmp_path / "outside")
    with pytest.raises(SnapshotError, match="symlink_or_special"):
        run._paths(source)
    output = tmp_path / "output"
    output.symlink_to(source, target_is_directory=True)
    with pytest.raises(SnapshotError, match="unsafe_output_path"):
        run.export_run(tmp_path / "absent", tmp_path / "absent", [], output / "new", "a" * 40)
    assert not (source / "new").exists()
