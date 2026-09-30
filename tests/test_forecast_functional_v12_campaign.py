"""Tiny retained-byte integration: no fresh generator, no final data."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from test_forecast_functional_v12_recipe import fixture

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting import functional_v12_campaign as campaign
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.functional_v12_archive import seal_checkpoint
from retailops_ai.forecasting.functional_v12_exposure import exposure_inventory, reserve_plan
from retailops_ai.forecasting.functional_v12_recipe import (
    CohortObservation,
    FunctionalV12Policy,
    fit_recipe_v12,
)
from retailops_ai.forecasting.functional_v12_resources import resource_plan
from retailops_ai.forecasting.manifest_contract import SplitPolicy
from retailops_ai.forecasting.quality_v2_contract import QualityPolicyV2
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, read_json


def write(root, name, value):
    campaign._write(root, name, canonical_bytes(value) + b"\n")


def key_for(row, fold, role, day):
    key = json.loads(row.key)
    key[0], key[1], key[2], key[-1] = (
        fold,
        role,
        end_of_day(day).isoformat(),
        (day + timedelta(days=row.horizon)).isoformat(),
    )
    return canonical_bytes(key).decode()


def compact_row(row):
    return {
        "key": row.key,
        "fold": row.fold,
        "role": row.role,
        "origin": row.origin,
        "volume": row.volume,
        "category": row.category,
        "channel": row.channel,
        "horizon": row.horizon,
        "eligible": not row.reasons,
        "reasons": list(row.reasons),
        "actual": row.actual,
        "label_available_at": row.available,
        "baseline_points": row.points,
        "baseline_bands": row.bands,
    }


@pytest.fixture
def archived_campaign(tmp_path, monkeypatch):
    """Archive/recipe/metrics/exposure are real; source loaders are tested separately."""
    fold, raw = fixture("zero")
    folds = [fold.model_copy(update={"name": f"fold-{i}"}) for i in range(3)]
    split = SplitPolicy(folds=tuple(folds))
    policy = FunctionalV12Policy(zero_pooling="category")
    registry = tmp_path / "registry"
    code = campaign.campaign_code()
    monkeypatch.setattr(campaign, "campaign_code", lambda: code)
    provenance = {
        "code_files": {"source.py": "a" * 64},
        "dependency_files": {"lock": "b" * 64},
        "python_version": "3.11.15",
        "git_commit": "b" * 40,
        "code_state": "clean",
    }
    provenance.update(
        {
            name + "_sha256": canonical_sha256(provenance[name + "_files"])
            for name in ("code", "dependency")
        }
    )
    source_config = {
        "source_schema_version": "2.7.0",
        "snapshot_schema_version": "1.1.0",
        "generation": {"profile": "fixture"},
        "resolved_parameters": {"profile": "fixture"},
        "inventory_configuration_sha256": {"1": "a" * 64, "2": "b" * 64},
        "context": {"evaluated_at": fold.evaluation_cutoff.isoformat()},
    }
    resources = resource_plan(
        cohort_count=2,
        max_checkpoint_bytes=1024**2,
        max_expanded_checkpoint_bytes=16 * 1024**2,
        max_prediction_bytes_per_cohort=4 * 1024**2,
        max_scoring_rows_per_cohort=2000,
        pilot_receipt_sha256="0" * 64,
    )
    descriptor = {
        "version": "forecast-functional-cohort-plan-1.0.0",
        "seeds": [1, 2],
        "previously_used_seeds": [],
        "previously_used_source_ids": [],
        "previously_used_snapshot_ids": [],
        "method_policy": policy.model_dump(mode="json"),
        "split_policy": split.model_dump(mode="json"),
        "origin_window": OriginWindow(
            start=fold.train.start, end=fold.development_holdout.end
        ).model_dump(mode="json"),
        "quality_policy": QualityPolicyV2().model_dump(mode="json"),
        "required_dimensions": {
            "category": ["one", *[f"unused-{i}" for i in range(7)]],
            "channel": ["store"],
            "volume": ["high", "low", "medium", "zero"],
        },
        "source_configuration": source_config,
        "campaign_code": code,
        "remote_preparation": {
            "source_provenance": provenance,
            "ai_commit": "a" * 40,
            "source_commit": "b" * 40,
            "github_run_number": 1,
        },
        "holdout_metrics_evaluated_before_freeze": False,
        "exposure_registry": str(registry.resolve()),
        "registry_starting_exposure_ids": [],
        "resource_plan": resources,
    }
    freeze = campaign.make_freeze(descriptor)
    reserve_plan(registry, freeze)
    parents, checkpoints = [], []
    for seed in (1, 2):
        parent = tmp_path / f"parent-{seed}"
        roots = {name: parent / name for name in campaign.ROOTS}
        for path in roots.values():
            path.mkdir(parents=True)
        source_desc = {
            "schema_version": "2.7.0",
            "resolved_parameters": {"profile": "fixture", "seed": seed},
            "inventory_configuration_sha256": source_config["inventory_configuration_sha256"][
                str(seed)
            ],
            "context": source_config["context"],
            **{
                key: provenance[key]
                for key in ("code_sha256", "dependency_sha256", "python_version")
            },
        }
        source = {
            "dataset_id": "source-sha256-" + canonical_sha256(source_desc),
            "descriptor": source_desc,
            "provenance": provenance,
        }
        qualification_desc = {"parent_source_id": source["dataset_id"]}
        qualification = {
            "qualification_id": "inventory-labels-sha256-" + canonical_sha256(qualification_desc),
            "descriptor": qualification_desc,
        }
        snapshot_desc = {
            "qualification": qualification_desc,
            "parent_qualification_id": qualification["qualification_id"],
        }
        snapshot = {
            "source_dataset_id": source["dataset_id"],
            "snapshot_id": "snapshot-sha256-" + canonical_sha256(snapshot_desc),
            "schema_version": "1.1.0",
            "source": source,
            "descriptor": snapshot_desc,
        }
        write(roots["source"], "table.json", {"retained": "source fixture"})
        write(roots["source"], "configuration.json", {"seed": seed})
        write(roots["source"], "report.json", {"status": "passed"})
        source.update(
            artifacts={"table": campaign._ref(roots["source"], "table.json")},
            inventory_configuration=campaign._ref(roots["source"], "configuration.json"),
            reports={"report": campaign._ref(roots["source"], "report.json")},
        )
        write(roots["qualification"], "windows.json", {"retained": "qualification fixture"})
        write(roots["qualification"], "report.json", {"status": "passed"})
        qualification.update(
            windows=campaign._ref(roots["qualification"], "windows.json"),
            report=campaign._ref(roots["qualification"], "report.json"),
        )
        write(roots["source"], "dataset_manifest.v2.json", source)
        write(roots["qualification"], "qualification_manifest.json", qualification)
        write(roots["snapshot"], "snapshot_manifest.json", snapshot)
        size, digest = file_hash(roots["snapshot"], "snapshot_manifest.json")
        seal_desc = {
            "files": {"snapshot_manifest.json": {"size_bytes": size, "sha256": digest}},
            "full_source_verification": "passed",
            "simulation_truth": "excluded",
            "required_use_cases": ["forecast_source"],
            "source_schema_version": "1.1.0",
        }
        seal = {
            "seal_id": "forecast-source-seal-sha256-" + canonical_sha256(seal_desc),
            "descriptor": seal_desc,
        }
        write(roots["cohort"], "source_seal.json", seal)
        recipes, counts = {}, {}
        for selected_fold in folds:
            rows = [
                replace(
                    row,
                    key=key_for(
                        row, selected_fold.name, "validation", date.fromisoformat(row.origin[:10])
                    ),
                    fold=selected_fold.name,
                )
                for row in raw
            ]
            recipe = fit_recipe_v12(
                [CohortObservation(row, f"seed-{seed}") for row in rows], selected_fold, policy
            )
            name = f"recipes/{selected_fold.name}.json"
            write(roots["cohort"], name, recipe)
            recipes[selected_fold.name] = {
                "recipe_id": recipe["recipe_id"],
                "file": campaign._ref(roots["cohort"], name),
                "roles": {"train": {"total": 0}, "validation": {"total": len(rows)}},
            }
            for role in campaign.ROLES:
                chosen = rows
                if role == "development_holdout":
                    chosen = [
                        replace(
                            row,
                            key=key_for(
                                row,
                                selected_fold.name,
                                role,
                                selected_fold.development_holdout.start,
                            ),
                            role=role,
                            origin=end_of_day(selected_fold.development_holdout.start).isoformat(),
                            actual=500 if row.actual else 0,
                        )
                        for row in rows[:120]
                    ]
                write(
                    roots["cohort"],
                    f"compact/{selected_fold.name}-{role}.json",
                    {"rows": [compact_row(row) for row in chosen]},
                )
                counts[f"{selected_fold.name}:{role}:total"] = len(chosen)
                counts[f"{selected_fold.name}:{role}:eligible"] = len(chosen)
        compact = {"inputs_id": "compact-fixture-" + str(seed), "descriptor": {"counts": counts}}
        write(roots["cohort"], "compact/compact_manifest.json", compact)
        cohort_desc = {
            "cohort_id": f"seed-{seed}",
            "source_dataset_id": source["dataset_id"],
            "snapshot_id": snapshot["snapshot_id"],
            "source_seal_id": seal["seal_id"],
            "compact_parent": {"path": "compact", "inputs_id": compact["inputs_id"]},
            "recipes": recipes,
            "split_policy": descriptor["split_policy"],
            "origin_window": descriptor["origin_window"],
            "method_policy": descriptor["method_policy"],
            "complete_compact_counts": counts,
        }
        cohort = {
            "cohort_artifact_id": "forecast-cohort-sha256-" + canonical_sha256(cohort_desc),
            "descriptor": cohort_desc,
        }
        write(roots["cohort"], "cohort_manifest.json", cohort)
        semantic = {
            "status": "passed",
            "inputs_id": compact["inputs_id"],
            "source_seal_id": seal["seal_id"],
            "compact_manifest_sha256": file_hash(
                roots["cohort"] / "compact", "compact_manifest.json"
            )[1],
            "replay": "full_sealed_snapshot_projection_features_labels_memberships",
            "model_fits": 0,
            "holdout_metrics_evaluated": False,
        }
        replay = {
            "status": "passed",
            "cohort_artifact_id": cohort["cohort_artifact_id"],
            "cohort_manifest_sha256": file_hash(roots["cohort"], "cohort_manifest.json")[1],
            "recipes": {name: body["roles"] for name, body in recipes.items()},
            "fit_calls": 0,
            "roles_opened": ["train", "validation"],
            "compact_semantic_replay": "required_separate_campaign_receipt",
            "forecast_model_status": "not_ready",
        }
        write(roots["receipts"], "semantic-replay.json", semantic)
        write(roots["receipts"], "recipe-replay.json", replay)
        lineage = {
            "freeze_id": freeze["freeze_id"],
            "seed": seed,
            "cohort_id": f"seed-{seed}",
            "source_dataset_id": source["dataset_id"],
            "snapshot_id": snapshot["snapshot_id"],
            "prepared_cohort_id": cohort["cohort_artifact_id"],
            "semantic_replay": {
                "path": "receipts/semantic-replay.json",
                "sha256": file_hash(roots["receipts"], "semantic-replay.json")[1],
            },
            "recipe_replay": {
                "path": "receipts/recipe-replay.json",
                "sha256": file_hash(roots["receipts"], "recipe-replay.json")[1],
            },
            "scope": "cohort_preparation_only",
            "holdout_metrics_evaluated": False,
        }
        checkpoints.append(seal_checkpoint(roots, tmp_path / "archives", lineage=lineage))
        parents.append((roots, lineage))
    monkeypatch.setattr(
        campaign, "load_cohort", lambda root: read_json(root, "cohort_manifest.json")
    )
    monkeypatch.setattr(
        campaign, "load_compact_inputs", lambda root: read_json(root, "compact_manifest.json")
    )
    opened = []

    def iterator(root, compact, fold, role):
        assert role != "train"
        assert exposure_inventory(registry), "labels opened before durable exposure"
        parameter_ids = {
            row["descriptor"]["fitted_recipes_sha256"] for row in exposure_inventory(registry)
        }
        assert len(parameter_ids) == 1
        opened.append((root, fold, role))
        yield from read_json(root, f"{fold}-{role}.json")["rows"]

    monkeypatch.setattr(campaign, "iter_compact_rows", iterator)
    return {
        "checkpoints": checkpoints,
        "freeze": freeze,
        "registry": registry,
        "output": tmp_path / "scored",
        "parents": parents,
        "opened": opened,
    }


def score(prepared, **kwargs):
    return campaign.score_archives(
        prepared["checkpoints"],
        prepared["freeze"],
        prepared["registry"],
        prepared["output"],
        **kwargs,
    )


def test_all_recipes_precede_exposure_and_failures_survive_no_fit_replay(
    archived_campaign, monkeypatch
):
    prepared = archived_campaign
    destination = score(prepared)
    manifest = campaign.load_campaign(destination)
    metrics = read_json(destination, "metrics.json")
    assert manifest["descriptor"]["forecast_model_status"] == "not_ready"
    assert len(metrics["segments"]) == 224
    assert metrics["failed_reasons"]["absolute_normalized_mean_bias_exceeded"] > 0
    assert len(prepared["opened"]) == 12
    assert len(exposure_inventory(prepared["registry"])) == 2
    recipes = read_json(destination, "fitted_recipes.json")
    assert set(recipes) == {"seed-1", "seed-2"}
    for fold, ref in recipes["seed-1"].items():
        recipe = read_json(destination, ref["path"])
        assert recipe["pooled_mean_calibration"]["fold"] == fold
        assert len(recipe["pooled_mean_calibration"]["source_recipe_ids"]) == 2

    def forbidden(*args, **kwargs):
        raise AssertionError("replay attempted to refit/merge parameters")

    monkeypatch.setattr(campaign, "merge_mean_calibration", forbidden)
    replay = score(prepared, replay=destination)
    proof = read_json(replay, "replay_receipt.json")["descriptor"]
    assert proof["status"] == "passed" and proof["refit_calls"] == 0
    assert proof["new_qualification"] is False
    assert len(proof["prediction_files"]) == 2
    assert not (replay / "predictions").exists()
    assert len(exposure_inventory(prepared["registry"])) == 2
    with pytest.raises(SnapshotError, match="seen_holdout_requires_explicit_replay"):
        score(prepared)


def test_missing_or_duplicate_inventory_blocks_all_label_reads(archived_campaign):
    prepared = archived_campaign
    with pytest.raises(SnapshotError, match="incomplete_or_duplicate"):
        campaign.score_archives(
            prepared["checkpoints"][:1],
            prepared["freeze"],
            prepared["registry"],
            prepared["output"],
        )
    prepared["checkpoints"][1] = prepared["checkpoints"][0]
    with pytest.raises(SnapshotError, match="incomplete_or_duplicate"):
        score(prepared)
    assert prepared["opened"] == [] and exposure_inventory(prepared["registry"]) == []


def test_bad_last_semantic_receipt_prevents_first_holdout(archived_campaign):
    prepared = archived_campaign
    roots, lineage = prepared["parents"][1]
    bad = read_json(roots["receipts"], "semantic-replay.json")
    bad["inputs_id"] = "wrong-parent"
    (roots["receipts"] / "semantic-replay.json").write_bytes(canonical_bytes(bad) + b"\n")
    lineage["semantic_replay"]["sha256"] = file_hash(roots["receipts"], "semantic-replay.json")[1]
    prepared["checkpoints"][1] = seal_checkpoint(
        roots, prepared["checkpoints"][0].parent, lineage=lineage
    )
    with pytest.raises(SnapshotError, match="full_semantic_replay_binding"):
        score(prepared)
    assert prepared["opened"] == [] and exposure_inventory(prepared["registry"]) == []
    assert list(prepared["output"].glob("failed-*/failure.json"))


def test_quality_code_registry_and_source_inventory_cannot_drift(archived_campaign):
    prepared = archived_campaign
    for field, value in (
        ("quality_policy", {}),
        ("campaign_code", {}),
        ("registry_starting_exposure_ids", ["bad"]),
        (
            "source_configuration",
            prepared["freeze"]["descriptor"]["source_configuration"]
            | {"inventory_configuration_sha256": {"1": "a" * 64}},
        ),
    ):
        changed = deepcopy(prepared["freeze"]["descriptor"])
        changed[field] = value
        with pytest.raises((SnapshotError, KeyError)):
            campaign.make_freeze(changed)
    with pytest.raises(SnapshotError, match="registry_binding"):
        campaign.score_archives(
            prepared["checkpoints"],
            prepared["freeze"],
            prepared["registry"].parent / "new-registry",
            prepared["output"],
        )


def test_prediction_byte_cap_preserves_failed_output_and_exposure(archived_campaign, monkeypatch):
    prepared = archived_campaign
    changed = deepcopy(prepared["freeze"]["descriptor"])
    changed["resource_plan"] = resource_plan(
        cohort_count=2,
        max_checkpoint_bytes=1024**2,
        max_expanded_checkpoint_bytes=16 * 1024**2,
        max_prediction_bytes_per_cohort=1,
        max_scoring_rows_per_cohort=2000,
        pilot_receipt_sha256="0" * 64,
    )
    changed["exposure_registry"] = str((prepared["registry"].parent / "cap-registry").resolve())
    prepared["freeze"] = campaign.make_freeze(changed)
    prepared["registry"] = Path(changed["exposure_registry"])
    reserve_plan(prepared["registry"], prepared["freeze"])
    for index, (roots, lineage) in enumerate(prepared["parents"]):
        lineage["freeze_id"] = prepared["freeze"]["freeze_id"]
        prepared["checkpoints"][index] = seal_checkpoint(
            roots, prepared["checkpoints"][0].parent, lineage=lineage
        )
    monkeypatch.setattr(
        campaign,
        "iter_compact_rows",
        lambda root, manifest, fold, role: iter(read_json(root, f"{fold}-{role}.json")["rows"]),
    )
    with pytest.raises(SnapshotError, match="prediction_byte_cap"):
        score(prepared)
    assert len(exposure_inventory(prepared["registry"])) == 1
    failures = list(prepared["output"].glob("failed-*"))
    assert (failures[0] / "fitted_recipes.json").exists()
    assert list(failures[0].glob("predictions/*.gz"))


def test_technical_failure_resumes_with_same_parameters_and_idempotent_exposure(
    archived_campaign, monkeypatch
):
    prepared = archived_campaign
    original = campaign._score_cohort

    def interrupted(root, record, *args):
        if record["seed"] == 2:
            raise OSError("fixture interruption after one complete cohort")
        return original(root, record, *args)

    monkeypatch.setattr(campaign, "_score_cohort", interrupted)
    with pytest.raises(OSError, match="fixture interruption"):
        score(prepared)
    failed = next(prepared["output"].glob("failed-*"))
    first_exposure = exposure_inventory(prepared["registry"])
    assert len(first_exposure) == 1
    assert (failed / "completed-cohorts/seed-1.json").exists()
    assert not (failed / "completed-cohorts/seed-2.json").exists()
    monkeypatch.setattr(campaign, "_score_cohort", original)
    completed = score(prepared, resume=failed)
    manifest = campaign.load_campaign(completed)
    assert (
        manifest["descriptor"]["fitted_recipes_sha256"]
        == first_exposure[0]["descriptor"]["fitted_recipes_sha256"]
    )
    assert first_exposure[0] in exposure_inventory(prepared["registry"])
    assert len(exposure_inventory(prepared["registry"])) == 2
    assert (failed / "failure.json").exists()
    assert (
        read_json(completed, "metrics.json")["failed_reasons"][
            "absolute_normalized_mean_bias_exceeded"
        ]
        > 0
    )
    # The saved recipe is authoritative only when it matches the frozen sufficient statistics.
    ref = next(iter(read_json(failed, "fitted_recipes.json")["seed-1"].values()))
    path = failed / ref["path"]
    path.write_bytes(path.read_bytes() + b" ")
    opened = len(prepared["opened"])
    with pytest.raises(SnapshotError, match="resume_recipe_bytes_changed"):
        score(prepared, resume=failed)
    assert len(prepared["opened"]) == opened


def test_truncated_role_cannot_be_reported_as_complete(archived_campaign, monkeypatch):
    prepared = archived_campaign
    original = campaign.iter_compact_rows

    def truncated(root, manifest, fold, role):
        rows = list(original(root, manifest, fold, role))
        yield from rows[:-1]

    monkeypatch.setattr(campaign, "iter_compact_rows", truncated)
    with pytest.raises(SnapshotError, match="role_count_mismatch"):
        score(prepared)
    assert len(exposure_inventory(prepared["registry"])) == 1
    assert all(role == "validation" for _, _, role in prepared["opened"])
    assert not list(prepared["output"].glob("functional-v12-campaign-*"))


def test_replay_detects_changed_predictions_without_new_exposure(archived_campaign, monkeypatch):
    prepared = archived_campaign
    destination = score(prepared)
    exposures = exposure_inventory(prepared["registry"])
    original = campaign.PreparedV12Predictor.predict

    def changed(self, row):
        candidate, baseline, metadata = original(self, row)
        return candidate.model_copy(update={"mean": candidate.mean + 1}), baseline, metadata

    monkeypatch.setattr(campaign.PreparedV12Predictor, "predict", changed)
    with pytest.raises(SnapshotError, match="replay_prediction_bytes_differ"):
        score(prepared, replay=destination)
    assert exposure_inventory(prepared["registry"]) == exposures


def test_disk_block_retains_receipt_before_any_label_iterator(archived_campaign, monkeypatch):
    prepared = archived_campaign
    monkeypatch.setattr(
        campaign,
        "disk_preflight",
        lambda *args, **kwargs: {
            "status": "blocked_insufficient_space",
            "quality_evaluated": False,
        },
    )
    with pytest.raises(SnapshotError, match="disk_preflight_blocked"):
        score(prepared)
    assert prepared["opened"] == [] and exposure_inventory(prepared["registry"]) == []
    assert list(prepared["output"].glob("failed-*/blocked-disk-preflight.json"))


@pytest.mark.parametrize(
    "namespace,name", [("source", "table.json"), ("qualification", "windows.json")]
)
def test_resealed_changed_raw_parent_bytes_cannot_hide_behind_same_manifests(
    archived_campaign, namespace, name
):
    prepared = archived_campaign
    roots, lineage = prepared["parents"][1]
    (roots[namespace] / name).write_bytes(b'{"changed":true}\n')
    prepared["checkpoints"][1] = seal_checkpoint(
        roots, prepared["checkpoints"][0].parent, lineage=lineage
    )
    with pytest.raises(SnapshotError, match="archived_" + namespace + "_file_bytes"):
        score(prepared)
    assert prepared["opened"] == [] and exposure_inventory(prepared["registry"]) == []


@pytest.mark.parametrize("mutation", ["fingerprint_only", "dirty", "different_commit"])
def test_freeze_requires_full_clean_source_commit_provenance(archived_campaign, mutation):
    descriptor = deepcopy(archived_campaign["freeze"]["descriptor"])
    provenance = descriptor["remote_preparation"]["source_provenance"]
    if mutation == "fingerprint_only":
        del provenance["git_commit"], provenance["code_state"]
    elif mutation == "dirty":
        provenance["code_state"] = "dirty"
    else:
        provenance["git_commit"] = "c" * 40
    with pytest.raises(SnapshotError, match="frozen_source_provenance"):
        campaign.make_freeze(descriptor)


def test_checkpoint_receipt_includes_manifest_storage(archived_campaign):
    path = archived_campaign["checkpoints"][0]
    manifest = campaign.verify_checkpoint(path)
    receipt = campaign._checkpoint_receipt(path, manifest)
    assert receipt["checkpoint_total_bytes"] == sum(file.stat().st_size for file in path.iterdir())
    assert (
        receipt["checkpoint_manifest"]["sha256"] == file_hash(path, "checkpoint_manifest.json")[1]
    )


@pytest.mark.parametrize("value", [None, False, True, 0, -1, 1.0, "1"])
def test_freeze_requires_positive_integer_github_run_number(archived_campaign, value):
    descriptor = deepcopy(archived_campaign["freeze"]["descriptor"])
    if value is None:
        del descriptor["remote_preparation"]["github_run_number"]
    else:
        descriptor["remote_preparation"]["github_run_number"] = value
    with pytest.raises(SnapshotError, match="frozen_github_run_number"):
        campaign.make_freeze(descriptor)
