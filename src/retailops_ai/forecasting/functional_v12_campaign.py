"""Two-pass, bounded qualification of all preregistered independent cohorts.

Pass one verifies every archived parent and freezes every pooled recipe before any
holdout iterator opens. Pass two persists exposure before reading held labels.
Replay uses saved recipes, never fitting, and compares all prediction/metric bytes.
"""

from __future__ import annotations

import gzip
import hashlib
import re
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import metadata
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from retailops_ai.curated.builder import implementation as curated_implementation
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.functional_models import functional_code
from retailops_ai.forecasting.functional_v12_archive import restore_checkpoint, verify_checkpoint
from retailops_ai.forecasting.functional_v12_cohort import load_cohort, observation_from_compact
from retailops_ai.forecasting.functional_v12_exposure import (
    exposure_inventory,
    open_holdout,
    require_complete_exposure,
)
from retailops_ai.forecasting.functional_v12_exposure import (
    make_freeze as identity_freeze,
)
from retailops_ai.forecasting.functional_v12_inputs import iter_compact_rows, load_compact_inputs
from retailops_ai.forecasting.functional_v12_quality import (
    CampaignObservation,
    StreamingCampaignScorer,
)
from retailops_ai.forecasting.functional_v12_recipe import (
    FunctionalV12Policy,
    PreparedV12Predictor,
    bind_pooled_mean,
    merge_mean_calibration,
)
from retailops_ai.forecasting.functional_v12_resources import disk_preflight, validate_resource_plan
from retailops_ai.forecasting.manifest_contract import SplitPolicy
from retailops_ai.forecasting.models import model_code
from retailops_ai.forecasting.quality_v2_contract import ProtocolObservation, QualityPolicyV2
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, inventory, read_json
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

VERSION = "forecast-functional-pooled-campaign-1.0.0"
ROOTS = {"source", "qualification", "snapshot", "cohort", "receipts"}
ROLES: tuple[Literal["validation", "development_holdout"], ...] = (
    "validation",
    "development_holdout",
)


def campaign_code() -> dict[str, Any]:
    """Pin the complete shared implementation and locked numerical dependencies."""
    package = Path(str(files("retailops_ai")))
    hashes = {
        path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in ("forecasting", "source_snapshot", "curated", "data_contracts")
        for path in sorted((package / directory).rglob("*.py"))
    }
    environment = model_code()
    return {
        "version": VERSION,
        "code_files": hashes,
        "code_sha256": canonical_sha256(hashes),
        "dependency_lock_sha256": environment.dependency_lock_sha256,
        "versions": environment.versions
        | {name: metadata.version(name) for name in ("pydantic", "jsonschema")},
        "curated_implementations": {
            version: curated_implementation(version) for version in ("1.0.0", "1.1.0")
        },
        "frozen_v11_code": functional_code(),
    }


def make_freeze(descriptor: dict[str, Any]) -> dict[str, Any]:
    """Validate a complete immutable plan; seed reservation precedes generation elsewhere."""
    freeze = identity_freeze(descriptor)
    _validate_freeze(freeze)
    return freeze


def _validate_freeze(freeze: dict[str, Any]) -> None:
    desc = freeze["descriptor"]
    if identity_freeze(desc) != freeze or len(desc["seeds"]) > 64:
        raise SnapshotError("pooled_campaign_freeze_identity_or_seed_budget")
    policy = FunctionalV12Policy.model_validate_json(canonical_bytes(desc["method_policy"]))
    split = SplitPolicy.model_validate_json(canonical_bytes(desc["split_policy"]))
    if policy.mean_weights is not None:
        expected_weights = {
            (fold.name, volume, category)
            for fold in split.folds
            for volume in desc["required_dimensions"]["volume"]
            if volume != "zero"
            for category in desc["required_dimensions"]["category"]
        }
        if set(policy.mean_weights.lookup()) != expected_weights:
            raise SnapshotError("pooled_campaign_frozen_mean_weight_inventory")
    window = OriginWindow.model_validate_json(canonical_bytes(desc["origin_window"]))
    if (
        desc["method_policy"] != policy.model_dump(mode="json")
        or desc["split_policy"] != split.model_dump(mode="json")
        or desc["origin_window"] != window.model_dump(mode="json")
        or desc["quality_policy"] != QualityPolicyV2().model_dump(mode="json")
        or desc["campaign_code"] != campaign_code()
        or not isinstance(desc["remote_preparation"], dict)
        or not desc["remote_preparation"]
        or not isinstance(desc["source_configuration"], dict)
        or not desc["source_configuration"]
        or any(
            window.start > fold.train.start or window.end < fold.development_holdout.end
            for fold in split.folds
        )
        or "pooled" in {fold.name for fold in split.folds}
    ):
        raise SnapshotError("pooled_campaign_freeze_policy_code_or_window")
    run_number = desc["remote_preparation"].get("github_run_number")
    if type(run_number) is not int or run_number <= 0:
        raise SnapshotError("pooled_campaign_frozen_github_run_number")
    for role in ("ai", "source"):
        environment = desc["remote_preparation"].get(role + "_environment", {})
        if (
            set(environment) != {"python_version", "packages"}
            or environment["python_version"] != "3.11.15"
            or not isinstance(environment["packages"], dict)
            or not environment["packages"]
            or any(
                not isinstance(name, str) or not name or not isinstance(version, str) or not version
                for name, version in environment["packages"].items()
            )
        ):
            raise SnapshotError("pooled_campaign_frozen_runtime_environment")
    dimensions = desc["required_dimensions"]
    if (
        set(dimensions) != {"category", "channel", "volume"}
        or any(
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value or len(value) > 256 for value in values)
            or values != sorted(set(values))
            for values in dimensions.values()
        )
        or dimensions["volume"] != sorted(QualityPolicyV2().required_volume_bins)
    ):
        raise SnapshotError("pooled_campaign_frozen_dimensions")
    for field, prefix in (
        ("previously_used_source_ids", "source-sha256-"),
        ("previously_used_snapshot_ids", "snapshot-sha256-"),
        ("registry_starting_exposure_ids", "cohort-exposure-sha256-"),
    ):
        values = desc[field]
        if (
            not isinstance(values, list)
            or values != sorted(set(values))
            or any(not isinstance(value, str) or not value.startswith(prefix) for value in values)
        ):
            raise SnapshotError("pooled_campaign_previous_inventory")
    registry = Path(desc["exposure_registry"])
    if not registry.is_absolute() or str(registry.resolve()) != str(registry):
        raise SnapshotError("pooled_campaign_registry_must_be_frozen_absolute_path")
    validate_resource_plan(desc["resource_plan"])
    configuration = desc["source_configuration"]
    if (
        desc["resource_plan"]["cohort_count"] != len(desc["seeds"])
        or set(configuration["inventory_configuration_sha256"])
        != {str(seed) for seed in desc["seeds"]}
        or "seed" in configuration["resolved_parameters"]
        or any(
            configuration["resolved_parameters"].get(key) != value
            for key, value in configuration["generation"].items()
        )
        or any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in configuration["inventory_configuration_sha256"].values()
        )
    ):
        raise SnapshotError("pooled_campaign_resource_or_source_seed_inventory")
    provenance = desc["remote_preparation"]["source_provenance"]
    if (
        set(provenance)
        != {
            "code_files",
            "code_sha256",
            "dependency_files",
            "dependency_sha256",
            "python_version",
            "git_commit",
            "code_state",
        }
        or not isinstance(provenance.get("git_commit"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", provenance["git_commit"])
        or provenance["git_commit"] != desc["remote_preparation"].get("source_commit")
        or provenance["code_state"] != "clean"
        or not provenance["code_files"]
        or not provenance["dependency_files"]
        or any(
            canonical_sha256(provenance[name + "_files"]) != provenance[name + "_sha256"]
            for name in ("code", "dependency")
        )
        or not provenance["python_version"]
    ):
        raise SnapshotError("pooled_campaign_frozen_source_provenance")


def _registry(registry: Path, freeze: dict[str, Any]) -> list[dict[str, Any]]:
    desc = freeze["descriptor"]
    if str(registry.resolve()) != desc["exposure_registry"]:
        raise SnapshotError("pooled_campaign_registry_binding")
    if read_json(registry, "plan-" + freeze["freeze_id"] + ".json") != freeze:
        raise SnapshotError("pooled_campaign_unreserved_plan")
    receipts = exposure_inventory(registry)
    current = {row["exposure_id"] for row in receipts}
    prior = set(desc["registry_starting_exposure_ids"])
    if not prior <= current or any(
        row["exposure_id"] not in prior and row["descriptor"]["freeze_id"] != freeze["freeze_id"]
        for row in receipts
    ):
        raise SnapshotError("pooled_campaign_registry_inventory_changed")
    return receipts


def _ref(root: Path, name: str) -> dict[str, Any]:
    size, digest = file_hash(root, name)
    return {"path": name, "size_bytes": size, "sha256": digest}


def _write(root: Path, name: str, raw: bytes) -> None:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    write_private(root / name, raw)


def _checkpoint_receipt(path: Path, checkpoint: dict[str, Any]) -> dict[str, Any]:
    manifest = _ref(path, "checkpoint_manifest.json")
    return {
        "checkpoint_manifest": manifest,
        "checkpoint_total_bytes": checkpoint["archive"]["size_bytes"] + manifest["size_bytes"],
    }


def _verify_archived_refs(
    root: Path,
    namespace: str,
    manifest_name: str,
    refs: Sequence[dict[str, Any]],
    archived: dict[str, Any],
) -> None:
    expected = {
        f"{namespace}/{manifest_name}": {
            key: value
            for key, value in _ref(root / namespace, manifest_name).items()
            if key != "path"
        }
    }
    for ref in refs:
        name = namespace + "/" + ref["path"]
        if name in expected:
            raise SnapshotError("pooled_campaign_duplicate_source_artifact_reference")
        expected[name] = {key: ref[key] for key in ("size_bytes", "sha256")}
    actual = {name: ref for name, ref in archived.items() if name.startswith(namespace + "/")}
    if actual != expected:
        raise SnapshotError("pooled_campaign_archived_" + namespace + "_file_bytes")


def _receipt(root: Path, lineage: dict[str, Any], key: str, name: str) -> dict[str, Any]:
    if lineage[key] != {"path": name, "sha256": file_hash(root, name)[1]}:
        raise SnapshotError("pooled_campaign_replay_receipt_checksum")
    return read_json(root, name)


def _verify_parents(
    root: Path, checkpoint: dict[str, Any], cohort: dict[str, Any], freeze: dict[str, Any]
) -> None:
    """Bind sealed source bytes, generation parameters and both no-fit replay receipts."""
    desc, lineage = cohort["descriptor"], checkpoint["descriptor"]["lineage"]
    archived = checkpoint["descriptor"]["files"]
    remote = freeze["descriptor"]["remote_preparation"]
    source_stage = read_json(root / "receipts", "source-stage.json")
    preparation = read_json(root / "receipts", "preparation.json")
    if source_stage.get("environment") != remote["source_environment"] or any(
        preparation.get(role + "_environment") != remote[role + "_environment"]
        for role in ("ai", "source")
    ):
        raise SnapshotError("pooled_campaign_runtime_environment_binding")
    seal = read_json(root / "cohort", "source_seal.json")
    sealed = seal["descriptor"]
    snapshot_files = {
        name.removeprefix("snapshot/"): ref
        for name, ref in archived.items()
        if name.startswith("snapshot/")
    }
    if (
        snapshot_files != sealed["files"]
        or sealed["full_source_verification"] != "passed"
        or sealed["simulation_truth"] != "excluded"
        or sealed["required_use_cases"] != ["forecast_source"]
    ):
        raise SnapshotError("pooled_campaign_sealed_snapshot_bytes_or_scope")
    snapshot = read_json(root / "snapshot", "snapshot_manifest.json")
    source = snapshot["source"]
    if (
        snapshot["snapshot_id"] != desc["snapshot_id"]
        or snapshot["source_dataset_id"] != desc["source_dataset_id"]
        or snapshot["schema_version"] != sealed["source_schema_version"]
        or source["dataset_id"] != desc["source_dataset_id"]
        or source["descriptor"]["resolved_parameters"]["seed"] != lineage["seed"]
    ):
        raise SnapshotError("pooled_campaign_source_seed_or_parent")
    configuration = freeze["descriptor"]["source_configuration"]
    parameters = {
        key: value
        for key, value in source["descriptor"]["resolved_parameters"].items()
        if key != "seed"
    }
    if (
        configuration["snapshot_schema_version"] != snapshot["schema_version"]
        or configuration["source_schema_version"] != source["descriptor"]["schema_version"]
        or configuration["resolved_parameters"] != parameters
        or configuration["inventory_configuration_sha256"][str(lineage["seed"])]
        != source["descriptor"]["inventory_configuration_sha256"]
        or configuration["context"] != source["descriptor"]["context"]
    ):
        raise SnapshotError("pooled_campaign_source_configuration")
    source_manifests = [
        read_json(root, name)
        for name in archived
        if name.startswith("source/")
        and Path(name).name.startswith("dataset_manifest.")
        and name.endswith(".json")
    ]
    if source_manifests != [source]:
        raise SnapshotError("pooled_campaign_archived_source_binding")
    _verify_archived_refs(
        root,
        "source",
        "dataset_manifest.v2.json",
        [
            *source["artifacts"].values(),
            source["inventory_configuration"],
            *source["reports"].values(),
        ],
        archived,
    )
    provenance = freeze["descriptor"]["remote_preparation"]["source_provenance"]
    if source["provenance"] != provenance or any(
        source["descriptor"][key] != provenance[key]
        for key in ("code_sha256", "dependency_sha256", "python_version")
    ):
        raise SnapshotError("pooled_campaign_source_implementation_binding")
    qualification = read_json(root / "qualification", "qualification_manifest.json")
    if (
        qualification["descriptor"] != snapshot["descriptor"]["qualification"]
        or qualification["qualification_id"] != snapshot["descriptor"]["parent_qualification_id"]
    ):
        raise SnapshotError("pooled_campaign_archived_qualification_binding")
    _verify_archived_refs(
        root,
        "qualification",
        "qualification_manifest.json",
        [qualification["windows"], qualification["report"]],
        archived,
    )
    compact_root = root / "cohort" / desc["compact_parent"]["path"]
    compact = load_compact_inputs(compact_root)
    semantic = _receipt(root, lineage, "semantic_replay", "receipts/semantic-replay.json")
    expected_semantic = {
        "status": "passed",
        "inputs_id": compact["inputs_id"],
        "source_seal_id": desc["source_seal_id"],
        "compact_manifest_sha256": file_hash(compact_root, "compact_manifest.json")[1],
        "replay": "full_sealed_snapshot_projection_features_labels_memberships",
        "model_fits": 0,
        "holdout_metrics_evaluated": False,
    }
    if any(semantic.get(key) != value for key, value in expected_semantic.items()):
        raise SnapshotError("pooled_campaign_full_semantic_replay_binding")
    replay = _receipt(root, lineage, "recipe_replay", "receipts/recipe-replay.json")
    expected_replay = {
        "status": "passed",
        "cohort_artifact_id": cohort["cohort_artifact_id"],
        "cohort_manifest_sha256": file_hash(root / "cohort", "cohort_manifest.json")[1],
        "recipes": {fold: value["roles"] for fold, value in desc["recipes"].items()},
        "fit_calls": 0,
        "roles_opened": ["train", "validation"],
        "compact_semantic_replay": "required_separate_campaign_receipt",
        "forecast_model_status": "not_ready",
    }
    if replay != expected_replay:
        raise SnapshotError("pooled_campaign_recipe_replay_binding")


def _inspect(
    root: Path, checkpoint: dict[str, Any], freeze: dict[str, Any], checkpoint_path: Path
) -> dict[str, Any]:
    archive = checkpoint["descriptor"]
    lineage = archive["lineage"]
    seed = lineage["seed"]
    cohort = load_cohort(root / "cohort")
    desc, plan = cohort["descriptor"], freeze["descriptor"]
    limit = plan["resource_plan"]
    checkpoint_files = _checkpoint_receipt(checkpoint_path, checkpoint)
    scoring_rows = sum(
        desc["complete_compact_counts"].get(f"{fold}:{role}:total", 0)
        for fold in desc["recipes"]
        for role in ROLES
    )
    if (
        checkpoint_files["checkpoint_total_bytes"] > limit["max_checkpoint_bytes"]
        or archive["total_bytes"] > limit["max_expanded_checkpoint_bytes"]
        or scoring_rows > limit["max_scoring_rows_per_cohort"]
    ):
        raise SnapshotError("pooled_campaign_checkpoint_or_scoring_resource_cap")
    if (
        {name.split("/", 1)[0] for name in archive["files"]} != ROOTS
        or type(seed) is not int
        or seed not in plan["seeds"]
        or lineage["freeze_id"] != freeze["freeze_id"]
        or lineage["cohort_id"] != f"seed-{seed}"
        or desc["cohort_id"] != lineage["cohort_id"]
        or lineage["scope"] != "cohort_preparation_only"
        or lineage["holdout_metrics_evaluated"] is not False
        or lineage["prepared_cohort_id"] != cohort["cohort_artifact_id"]
        or any(lineage[key] != desc[key] for key in ("source_dataset_id", "snapshot_id"))
        or desc["split_policy"] != plan["split_policy"]
        or desc["origin_window"] != plan["origin_window"]
        or desc["method_policy"] != plan["method_policy"]
        or desc["source_dataset_id"] in plan["previously_used_source_ids"]
        or desc["snapshot_id"] in plan["previously_used_snapshot_ids"]
    ):
        raise SnapshotError("pooled_campaign_checkpoint_lineage")
    _verify_parents(root, checkpoint, cohort, freeze)
    recipes = {
        fold: read_json(root / "cohort", body["file"]["path"])
        for fold, body in desc["recipes"].items()
    }
    return {
        "seed": seed,
        "checkpoint_id": checkpoint["checkpoint_id"],
        "checkpoint_archive": checkpoint["archive"],
        **checkpoint_files,
        "cohort_artifact_id": cohort["cohort_artifact_id"],
        "descriptor": desc,
        "local_recipes": recipes,
    }


def _check_complete(records: Sequence[dict[str, Any]], freeze: dict[str, Any]) -> None:
    if sorted(row["seed"] for row in records) != freeze["descriptor"]["seeds"]:
        raise SnapshotError("pooled_campaign_incomplete_or_duplicate_seed_inventory")
    for key in ("source_dataset_id", "snapshot_id", "cohort_id"):
        if len({row["descriptor"][key] for row in records}) != len(records):
            raise SnapshotError("pooled_campaign_nonindependent_cohorts")


def _freeze_recipes(records: Sequence[dict[str, Any]], root: Path) -> tuple[dict[str, Any], str]:
    folds = sorted(records[0]["local_recipes"])
    shared = {
        fold: merge_mean_calibration([row["local_recipes"][fold] for row in records])
        for fold in folds
    }
    result: dict[str, Any] = {}
    for row in records:
        cohort_id = row["descriptor"]["cohort_id"]
        result[cohort_id] = {}
        for fold in folds:
            recipe = bind_pooled_mean(row["local_recipes"][fold], shared[fold])
            name = f"recipes/{cohort_id}/{fold}.json"
            _write(root, name, canonical_bytes(recipe) + b"\n")
            result[cohort_id][fold] = _ref(root, name) | {"recipe_id": recipe["recipe_id"]}
    for fold, calibration in shared.items():
        _write(root, f"calibration/{fold}.json", canonical_bytes(calibration) + b"\n")
    write_private(root / "fitted_recipes.json", canonical_bytes(result) + b"\n")
    fsync_tree(root)
    return result, canonical_sha256(result)


def _load_saved_recipes(
    saved: Path, records: Sequence[dict[str, Any]], root: Path, freeze: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    manifest = load_campaign(saved)
    desc = manifest["descriptor"]
    if desc["freeze"] != freeze or desc["cohorts"] != _cohort_receipts(records):
        raise SnapshotError("pooled_campaign_replay_parent_binding")
    recipes = read_json(saved, "fitted_recipes.json")
    if canonical_sha256(recipes) != desc["fitted_recipes_sha256"]:
        raise SnapshotError("pooled_campaign_replay_recipe_inventory")
    for row in records:
        cohort_id = row["descriptor"]["cohort_id"]
        if set(recipes[cohort_id]) != set(row["local_recipes"]):
            raise SnapshotError("pooled_campaign_replay_fold_inventory")
        for fold, ref in recipes[cohort_id].items():
            recipe = read_json(saved, ref["path"])
            PreparedV12Predictor(recipe)
            if (
                recipe["recipe_id"] != ref["recipe_id"]
                or recipe["reference_recipe"] != row["local_recipes"][fold]["reference_recipe"]
                or recipe["policy"] != freeze["descriptor"]["method_policy"]
            ):
                raise SnapshotError("pooled_campaign_replay_saved_recipe_binding")
            _write(root, ref["path"], canonical_bytes(recipe) + b"\n")
    if set(recipes) != {row["descriptor"]["cohort_id"] for row in records}:
        raise SnapshotError("pooled_campaign_replay_cohort_inventory")
    for name in desc["files"]:
        if name.startswith("calibration/"):
            _write(root, name, (saved / name).read_bytes())
    write_private(root / "fitted_recipes.json", canonical_bytes(recipes) + b"\n")
    fsync_tree(root)
    return recipes, desc["fitted_recipes_sha256"]


def _cohort_receipts(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in row.items() if key != "local_recipes"} for row in records]


def _load_resume_recipes(
    saved: Path, records: Sequence[dict[str, Any]], root: Path, freeze: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    """Verify exact saved parameters against immutable sufficient statistics, without labels."""
    failure = read_json(saved, "failure.json")
    if (
        failure["freeze_id"] != freeze["freeze_id"]
        or failure["status"] != "not_ready"
        or read_json(saved, "freeze.json") != freeze
    ):
        raise SnapshotError("pooled_campaign_resume_freeze_or_failure_binding")
    recipes, digest = _freeze_recipes(records, root)
    expected_preparation = {
        "freeze_id": freeze["freeze_id"],
        "cohorts": _cohort_receipts(records),
        "fitted_recipes_sha256": digest,
        "all_final_recipes_saved_before_holdout_open": True,
    }
    if read_json(saved, "preparation_manifest.json") != expected_preparation:
        raise SnapshotError("pooled_campaign_resume_checkpoint_or_parent_changed")
    if read_json(saved, "fitted_recipes.json") != recipes or file_hash(
        saved, "fitted_recipes.json"
    ) != file_hash(root, "fitted_recipes.json"):
        raise SnapshotError("pooled_campaign_resume_parameters_changed")
    for cohort_recipes in recipes.values():
        for ref in cohort_recipes.values():
            if file_hash(saved, ref["path"]) != (ref["size_bytes"], ref["sha256"]):
                raise SnapshotError("pooled_campaign_resume_recipe_bytes_changed")
    return recipes, digest


def _score_cohort(
    root: Path,
    record: dict[str, Any],
    recipes: dict[str, Any],
    recipes_root: Path,
    target: Path,
    scorer: StreamingCampaignScorer,
    registry: Path,
    freeze: dict[str, Any],
    parameters_sha256: str,
) -> dict[str, Any]:
    desc = record["descriptor"]
    cohort_id = desc["cohort_id"]
    compact_root = root / "cohort" / desc["compact_parent"]["path"]
    compact = load_compact_inputs(compact_root)
    exposure = open_holdout(
        registry,
        freeze,
        seed=record["seed"],
        source_dataset_id=desc["source_dataset_id"],
        snapshot_id=desc["snapshot_id"],
        fitted_recipes_sha256=parameters_sha256,
    )
    counters: dict[str, Any] = {}
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    limits = freeze["descriptor"]["resource_plan"]
    total_scored = 0
    with (
        target.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as stream,
    ):
        for fold in sorted(recipes[cohort_id]):
            predictor = PreparedV12Predictor(
                read_json(recipes_root, recipes[cohort_id][fold]["path"])
            )
            reference = predictor.recipe["reference_recipe"]
            for role in ROLES:
                total = eligible = 0
                for value in iter_compact_rows(compact_root, compact, fold, role):
                    wrapped = observation_from_compact(value, cohort_id)
                    row = wrapped.observation
                    candidate, baseline, metadata = predictor.predict(wrapped)
                    if (
                        candidate.median != baseline.median
                        or candidate.interval != baseline.interval
                    ):
                        raise SnapshotError("pooled_campaign_exact_reference_changed")
                    cell = reference["calibration"].get(metadata["baseline"])
                    calibration_rows = cell["rows"] if cell else None
                    observation = ProtocolObservation(
                        key=row.key,
                        actual=row.actual,
                        exclusion_reasons=row.reasons,
                        candidate=candidate,
                        baseline=baseline,
                    )
                    scorer.add(
                        CampaignObservation(
                            cohort_id=cohort_id,
                            fold=fold,
                            role=role,
                            horizon=row.horizon,
                            category=row.category,
                            channel=row.channel,
                            volume=row.volume,
                            observation=observation,
                            retained_median_baseline=True,
                            candidate_calibration_rows=calibration_rows,
                            baseline_calibration_rows=calibration_rows,
                        )
                    )
                    payload = (
                        canonical_bytes(
                            {
                                "cohort_id": cohort_id,
                                "fold": fold,
                                "role": role,
                                "key": row.key,
                                "horizon": row.horizon,
                                "category": row.category,
                                "channel": row.channel,
                                "volume": row.volume,
                                "label_available_at": row.available,
                                "observation": observation.model_dump(mode="json"),
                                "metadata": metadata,
                                "calibration_rows": calibration_rows,
                            }
                        )
                        + b"\n"
                    )
                    stream.write(payload)
                    if raw.tell() > limits["max_prediction_bytes_per_cohort"]:
                        raise SnapshotError("pooled_campaign_prediction_byte_cap")
                    digest.update(payload)
                    total += 1
                    total_scored += 1
                    if total_scored > limits["max_scoring_rows_per_cohort"]:
                        raise SnapshotError("pooled_campaign_scoring_row_cap")
                    eligible += not row.reasons
                expected = compact["descriptor"]["counts"]
                if total != expected.get(f"{fold}:{role}:total", 0) or eligible != expected.get(
                    f"{fold}:{role}:eligible", 0
                ):
                    raise SnapshotError("pooled_campaign_role_count_mismatch")
                counters[f"{fold}:{role}"] = {"total": total, "eligible": eligible}
    if target.stat().st_size > limits["max_prediction_bytes_per_cohort"]:
        raise SnapshotError("pooled_campaign_prediction_byte_cap")
    return {
        "exposure": exposure,
        "counts": counters,
        "logical_predictions_sha256": digest.hexdigest(),
    }


def load_campaign(root: Path) -> dict[str, Any]:
    manifest = read_json(root, "campaign_manifest.json")
    desc = manifest["descriptor"]
    if (
        manifest["campaign_id"] != "functional-v12-campaign-sha256-" + canonical_sha256(desc)
        or desc["version"] != VERSION
        or desc["code"] != campaign_code()
    ):
        raise SnapshotError("pooled_campaign_manifest_identity_or_code")
    _validate_freeze(desc["freeze"])
    inventory(root, {"campaign_manifest.json", *desc["files"]})
    for name, ref in desc["files"].items():
        if _ref(root, name) != ref:
            raise SnapshotError("pooled_campaign_file_checksum")
    metrics = read_json(root, "metrics.json")
    if (
        desc["forecast_model_status"] != "not_ready"
        or desc["quality_qualification_status"] != metrics["status"]
        or desc["fitted_recipes_sha256"] != canonical_sha256(read_json(root, "fitted_recipes.json"))
    ):
        raise SnapshotError("pooled_campaign_status_or_fitted_parameters_binding")
    return manifest


def _preflight(
    checkpoints: Sequence[Path],
    manifests: Sequence[dict[str, Any]],
    freeze: dict[str, Any],
    root: Path,
    replay: Path | None,
) -> dict[str, Any]:
    plan = freeze["descriptor"]["resource_plan"]
    if len({path.parent.stat().st_dev for path in checkpoints}) != 1:
        raise SnapshotError("pooled_campaign_archive_volumes_must_match")
    predictions = 0
    if replay is not None:
        previous = load_campaign(replay)
        predictions = sum(
            ref["size_bytes"]
            for name, ref in previous["descriptor"]["files"].items()
            if name.startswith("predictions/")
        )
    result = disk_preflight(
        plan,
        archive_volume=checkpoints[0].parent,
        work_volume=root,
        retained_checkpoint_bytes=sum(
            _checkpoint_receipt(path, manifest)["checkpoint_total_bytes"]
            for path, manifest in zip(checkpoints, manifests, strict=True)
        ),
        retained_prediction_bytes=predictions,
    )
    if result["status"] != "passed":
        _write(root, "blocked-disk-preflight.json", canonical_bytes(result) + b"\n")
        raise SnapshotError("pooled_campaign_disk_preflight_blocked")
    return result


def _check_exposure_binding(
    records: Sequence[dict[str, Any]],
    existing: Sequence[dict[str, Any]],
    freeze: dict[str, Any],
    digest: str,
) -> None:
    for record in records:
        desc = record["descriptor"]
        binding = {
            "freeze_id": freeze["freeze_id"],
            "seed": record["seed"],
            "source_dataset_id": desc["source_dataset_id"],
            "snapshot_id": desc["snapshot_id"],
            "fitted_recipes_sha256": digest,
        }
        for receipt in existing:
            previous = receipt["descriptor"]
            if any(
                previous[key] == binding[key]
                for key in ("seed", "source_dataset_id", "snapshot_id")
            ) and any(previous[key] != value for key, value in binding.items()):
                raise SnapshotError("pooled_campaign_preexisting_exposure_conflict")


def _execute(
    checkpoints: Sequence[Path],
    freeze: dict[str, Any],
    registry: Path,
    root: Path,
    replay: Path | None,
    resume: Path | None,
) -> tuple[str, str]:
    existing_exposures = _registry(registry, freeze)
    manifests = [verify_checkpoint(path) for path in checkpoints]
    limits = freeze["descriptor"]["resource_plan"]
    if any(
        _checkpoint_receipt(path, manifest)["checkpoint_total_bytes"]
        > limits["max_checkpoint_bytes"]
        or manifest["descriptor"]["total_bytes"] > limits["max_expanded_checkpoint_bytes"]
        for path, manifest in zip(checkpoints, manifests, strict=True)
    ):
        raise SnapshotError("pooled_campaign_checkpoint_resource_cap")
    _preflight(checkpoints, manifests, freeze, root, replay)
    records = []
    paths: dict[int, Path] = {}
    for checkpoint_path, checkpoint in zip(checkpoints, manifests, strict=True):
        with tempfile.TemporaryDirectory(prefix="cohort-inspect-", dir=root) as restored:
            restored_root = restore_checkpoint(checkpoint_path, Path(restored))
            record = _inspect(restored_root, checkpoint, freeze, checkpoint_path)
        records.append(record)
        paths[record["seed"]] = checkpoint_path
    records.sort(key=lambda row: row["seed"])
    _check_complete(records, freeze)
    if resume is not None:
        recipes, parameters_sha256 = _load_resume_recipes(resume, records, root, freeze)
    elif replay is None:
        if any(row["descriptor"]["freeze_id"] == freeze["freeze_id"] for row in existing_exposures):
            raise SnapshotError("pooled_campaign_seen_holdout_requires_explicit_replay")
        recipes, parameters_sha256 = _freeze_recipes(records, root)
    else:
        recipes, parameters_sha256 = _load_saved_recipes(replay, records, root, freeze)
        exposures = require_complete_exposure(registry, freeze)
        if any(
            row["descriptor"]["fitted_recipes_sha256"] != parameters_sha256 for row in exposures
        ):
            raise SnapshotError("pooled_campaign_replay_parameter_exposure")
    _check_exposure_binding(records, existing_exposures, freeze, parameters_sha256)
    # All recipes and their common fingerprint exist durably before the first exposure.
    write_private(root / "freeze.json", canonical_bytes(freeze) + b"\n")
    write_private(
        root / "preparation_manifest.json",
        canonical_bytes(
            {
                "freeze_id": freeze["freeze_id"],
                "cohorts": _cohort_receipts(records),
                "fitted_recipes_sha256": parameters_sha256,
                "all_final_recipes_saved_before_holdout_open": True,
            }
        )
        + b"\n",
    )
    fsync_tree(root)
    if (
        sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        > limits["max_campaign_metadata_bytes"]
    ):
        raise SnapshotError("pooled_campaign_metadata_resource_cap")
    _preflight(checkpoints, manifests, freeze, root, replay)
    results = {}
    comparison: dict[str, Any] = {}
    previous = load_campaign(replay) if replay is not None else None
    split = SplitPolicy.model_validate_json(canonical_bytes(freeze["descriptor"]["split_policy"]))
    with StreamingCampaignScorer(
        cohorts=[row["descriptor"]["cohort_id"] for row in records],
        folds=[fold.name for fold in split.folds],
        dimensions=freeze["descriptor"]["required_dimensions"],
        max_rows=len(records) * limits["max_scoring_rows_per_cohort"],
        max_index_bytes=limits["max_index_bytes"],
        temporary_directory=root,
    ) as scorer:
        for record in records:
            path = paths[record["seed"]]
            checkpoint = verify_checkpoint(path)
            with tempfile.TemporaryDirectory(prefix="cohort-score-", dir=root) as restored:
                restored_root = restore_checkpoint(path, Path(restored))
                if _inspect(restored_root, checkpoint, freeze, path) != record:
                    raise SnapshotError("pooled_campaign_archive_changed_between_passes")
                cohort_id = record["descriptor"]["cohort_id"]
                target = (
                    root / f"predictions/{cohort_id}.jsonl.gz"
                    if replay is None
                    else Path(restored) / f"{cohort_id}.jsonl.gz"
                )
                results[cohort_id] = _score_cohort(
                    restored_root,
                    record,
                    recipes,
                    root,
                    target,
                    scorer,
                    registry,
                    freeze,
                    parameters_sha256,
                )
                if previous is not None:
                    expected = previous["descriptor"]["files"][f"predictions/{cohort_id}.jsonl.gz"]
                    size, digest = file_hash(target.parent, target.name)
                    if (size, digest) != (expected["size_bytes"], expected["sha256"]):
                        raise SnapshotError("pooled_campaign_replay_prediction_bytes_differ")
                    comparison[cohort_id] = {
                        "status": "passed",
                        "size_bytes": size,
                        "sha256": digest,
                    }
                else:
                    marker = {
                        "cohort_id": cohort_id,
                        "fitted_recipes_sha256": parameters_sha256,
                        "prediction": _ref(root, f"predictions/{cohort_id}.jsonl.gz"),
                        "result": results[cohort_id],
                    }
                    if (
                        resume is not None
                        and (resume / f"completed-cohorts/{cohort_id}.json").exists()
                    ):
                        saved_marker = read_json(resume, f"completed-cohorts/{cohort_id}.json")
                        if (
                            saved_marker != marker
                            or _ref(resume, saved_marker["prediction"]["path"])
                            != saved_marker["prediction"]
                        ):
                            raise SnapshotError(
                                "pooled_campaign_resume_completed_prediction_changed"
                            )
                    _write(
                        root, f"completed-cohorts/{cohort_id}.json", canonical_bytes(marker) + b"\n"
                    )
        metrics = scorer.finalize()
    exposures = require_complete_exposure(registry, freeze)
    if freeze["descriptor"]["campaign_code"] != campaign_code():
        raise SnapshotError("pooled_campaign_code_changed_during_evaluation")
    write_private(root / "metrics.json", canonical_bytes(metrics) + b"\n")
    write_private(root / "cohort_results.json", canonical_bytes(results) + b"\n")
    if replay is not None:
        if previous is None:
            raise SnapshotError("pooled_campaign_missing_replay_parent")
        for name in ("metrics.json", "cohort_results.json", "fitted_recipes.json"):
            if file_hash(root, name) != file_hash(replay, name):
                raise SnapshotError("pooled_campaign_replay_metric_or_parameter_bytes_differ")
        receipt = {
            "status": "passed",
            "scope": "independent_replay_of_same_frozen_exposed_campaign",
            "campaign_id": previous["campaign_id"],
            "freeze_id": freeze["freeze_id"],
            "fitted_recipes_sha256": parameters_sha256,
            "prediction_files": comparison,
            "metrics_sha256": file_hash(root, "metrics.json")[1],
            "all_preregistered_cohorts_included": True,
            "refit_calls": 0,
            "new_qualification": False,
            "forecast_model_status": "ready" if metrics["status"] == "passed" else "not_ready",
            "code": campaign_code(),
        }
        identifier = "functional-v12-replay-sha256-" + canonical_sha256(receipt)
        _write(
            root,
            "replay_receipt.json",
            canonical_bytes({"replay_id": identifier, "descriptor": receipt}) + b"\n",
        )
        return identifier, "replay_receipt.json"
    files_receipt = {
        p.relative_to(root).as_posix(): _ref(root, p.relative_to(root).as_posix())
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
    descriptor = {
        "version": VERSION,
        "freeze": freeze,
        "code": campaign_code(),
        "cohorts": _cohort_receipts(records),
        "fitted_recipes_sha256": parameters_sha256,
        "exposure_ids": sorted(row["exposure_id"] for row in exposures),
        "files": files_receipt,
        "forecast_model_status": "not_ready",
        "quality_qualification_status": metrics["status"],
        "independent_replay": "required_before_ready",
        "quality_thresholds_changed": False,
        "all_preregistered_cohorts_included": True,
        "all_final_recipes_saved_before_holdout_open": True,
        "training_rows_read_during_scoring": 0,
        "refit_calls": 0,
        "segment_counts": metrics["segment_counts"],
        "failed_reasons": metrics["failed_reasons"],
    }
    manifest = {
        "campaign_id": "functional-v12-campaign-sha256-" + canonical_sha256(descriptor),
        "descriptor": descriptor,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }
    write_private(root / "campaign_manifest.json", canonical_bytes(manifest) + b"\n")
    return str(manifest["campaign_id"]), "campaign_manifest.json"


def score_archives(
    checkpoints: Sequence[Path],
    freeze: dict[str, Any],
    registry: Path,
    output: Path,
    replay: Path | None = None,
    *,
    resume: Path | None = None,
) -> Path:
    """Score the frozen inventory, or retain one-cohort-at-a-time no-fit replay proof."""
    _validate_freeze(freeze)
    if replay is not None and resume is not None:
        raise SnapshotError("pooled_campaign_replay_and_resume_are_exclusive")
    if not checkpoints or len(checkpoints) != len(freeze["descriptor"]["seeds"]):
        raise SnapshotError("pooled_campaign_incomplete_or_duplicate_seed_inventory")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pooled-campaign-", dir=output) as temporary:
        root = Path(temporary)
        try:
            identifier, receipt = _execute(checkpoints, freeze, registry, root, replay, resume)
            fsync_tree(root)
            destination = output / identifier
            if destination.exists():
                if replay is None or read_json(destination, receipt) != read_json(root, receipt):
                    raise SnapshotError("pooled_campaign_output_already_exists")
            else:
                publish_noreplace(root, destination)
            return destination
        except BaseException as exc:
            _write(
                root,
                "failure.json",
                canonical_bytes(
                    {
                        "status": "not_ready",
                        "freeze_id": freeze["freeze_id"],
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "retained_outputs_not_qualified": True,
                    }
                )
                + b"\n",
            )
            fsync_tree(root)
            publish_noreplace(root, output / ("failed-" + root.name.lstrip(".")))
            raise
