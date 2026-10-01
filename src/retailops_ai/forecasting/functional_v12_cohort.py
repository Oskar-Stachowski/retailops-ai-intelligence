"""Bounded local cohort preparation; qualification and shared mean pooling are separate.

Only train/validation labels enter fitting. The compact parent keeps the complete
panel, including purged and holdout rows, but fitting and recipe replay never open
those role iterators. Replay checks saved prediction receipts without refitting.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.functional_recipe import Observation
from retailops_ai.forecasting.functional_v12_inputs import (
    build_compact_inputs,
    iter_compact_rows,
    load_compact_inputs,
    seal_snapshot,
)
from retailops_ai.forecasting.functional_v12_recipe import (
    CohortObservation,
    FunctionalV12Policy,
    PreparedV12Predictor,
    fit_recipe_v12,
)
from retailops_ai.forecasting.manifest_contract import FeaturePolicy, SplitPolicy
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    inventory,
    read_json,
)
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

DEVELOPMENT_ROLES: tuple[Literal["train", "validation"], ...] = ("train", "validation")
MAX_ROWS_PER_ROLE = 99999
VERSION = "forecast-functional-cohort-1.0.0"


def cohort_code() -> dict[str, Any]:
    """Pin the local fitter, prediction path and inherited frozen contracts."""
    names = (
        "functional_v12_cohort.py",
        "functional_v12_recipe.py",
        "functional_v12_inputs.py",
        "functional_recipe.py",
        "functional_contract.py",
        "quality_v2.py",
        "quality_v2_contract.py",
        "manifest_contract.py",
        "contract.py",
    )
    hashes = {
        "forecasting/" + name: hashlib.sha256(
            files("retailops_ai.forecasting").joinpath(name).read_bytes()
        ).hexdigest()
        for name in names
    }
    return {"version": VERSION, "code_files": hashes, "code_sha256": canonical_sha256(hashes)}


def observation_from_compact(row: dict[str, Any], cohort_id: str) -> CohortObservation:
    """Keep eligibility and as-of baselines; no source truth or inferred missing labels."""
    if bool(row["eligible"]) != (not row["reasons"]) or (
        not row["eligible"] and (row["actual"] is not None or row["label_available_at"] is not None)
    ):
        raise SnapshotError("functional_v12_cohort_eligibility_binding")
    return CohortObservation(
        Observation(
            key=row["key"],
            fold=row["fold"],
            role=row["role"],
            origin=row["origin"],
            volume=row["volume"],
            category=row["category"],
            channel=row["channel"],
            horizon=row["horizon"],
            reasons=tuple(row["reasons"]),
            actual=row["actual"],
            available=row["label_available_at"],
            points=dict(row["baseline_points"]),
            bands={
                k: tuple(v) if v is not None else None for k, v in row["baseline_bands"].items()
            },
        ),
        cohort_id,
    )


def _read_role(
    compact_root: Path,
    manifest: dict[str, Any],
    cohort_id: str,
    fold: str,
    role: Literal["train", "validation"],
) -> tuple[list[CohortObservation], dict[str, Any]]:
    if role not in DEVELOPMENT_ROLES:
        raise SnapshotError("functional_v12_cohort_fitting_role_forbidden")
    total = manifest["descriptor"]["counts"].get(f"{fold}:{role}:total", 0)
    if total > MAX_ROWS_PER_ROLE:
        raise SnapshotError("functional_v12_cohort_role_resource_budget")
    rows: list[CohortObservation] = []
    counts: Counter[str] = Counter()
    digest = hashlib.sha256()
    for row in iter_compact_rows(compact_root, manifest, fold, role):
        counts["total"] += 1
        if counts["total"] > MAX_ROWS_PER_ROLE:
            raise SnapshotError("functional_v12_cohort_role_resource_budget")
        if row["fold"] != fold or row["role"] != role:
            raise SnapshotError("functional_v12_cohort_role_binding")
        counts["eligible"] += bool(row["eligible"])
        counts.update("excluded:" + reason for reason in row["reasons"])
        digest.update(canonical_bytes(row) + b"\n")
        rows.append(observation_from_compact(row, cohort_id))
    if counts["total"] != total or counts["eligible"] != manifest["descriptor"]["counts"].get(
        f"{fold}:{role}:eligible", 0
    ):
        raise SnapshotError("functional_v12_cohort_role_counts")
    return rows, {
        "total": counts["total"],
        "eligible": counts["eligible"],
        "excluded": counts["total"] - counts["eligible"],
        "exclusion_counts": {
            k[9:]: v for k, v in sorted(counts.items()) if k.startswith("excluded:")
        },
        "joined_rows_sha256": digest.hexdigest(),
    }


def _prediction_receipt(
    rows: Iterable[CohortObservation],
    predictor: PreparedV12Predictor,
) -> dict[str, Any]:
    digest, count = hashlib.sha256(), 0
    for row in rows:
        candidate, baseline, metadata = predictor.predict(row)
        digest.update(
            canonical_bytes(
                {
                    "cohort_id": row.cohort_id,
                    "key": row.observation.key,
                    "candidate": candidate.model_dump(mode="json"),
                    "baseline": baseline.model_dump(mode="json"),
                    "metadata": metadata,
                }
            )
            + b"\n"
        )
        count += 1
    return {"rows": count, "predictions_sha256": digest.hexdigest()}


def fit_local_recipes(
    compact_root: Path,
    manifest: dict[str, Any],
    cohort_id: str,
    method_policy: FunctionalV12Policy,
    recipes_root: Path,
    *,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Fit each logical fold separately, using one bounded read of each development role."""
    if method_policy.mean_variant == "hgb_blend":
        raise SnapshotError("functional_v12_cohort_hgb_requires_explicit_pinned_model_provider")
    split = SplitPolicy.model_validate_json(canonical_bytes(manifest["descriptor"]["split_policy"]))
    result = {}
    recipes_root.mkdir(parents=True, exist_ok=True)
    for fold in split.folds:
        train, train_counts = _read_role(compact_root, manifest, cohort_id, fold.name, "train")
        validation, validation_counts = _read_role(
            compact_root, manifest, cohort_id, fold.name, "validation"
        )
        recipe = fit_recipe_v12(
            [r for r in validation if not r.observation.reasons],
            fold,
            method_policy,
            training_rows=[r for r in train if not r.observation.reasons],
        )
        predictor = PreparedV12Predictor(recipe)
        roles = {
            "train": train_counts | _prediction_receipt(train, predictor),
            "validation": validation_counts | _prediction_receipt(validation, predictor),
        }
        name = fold.name + ".json"
        write_private(recipes_root / name, canonical_bytes(recipe) + b"\n")
        size, digest = file_hash(recipes_root, name)
        result[fold.name] = {
            "recipe_id": recipe["recipe_id"],
            "file": {"path": "recipes/" + name, "size_bytes": size, "sha256": digest},
            "roles": roles,
        }
        if progress is not None:
            progress(
                {
                    "stage": "local_recipe_fitted",
                    "cohort_id": cohort_id,
                    "fold": fold.name,
                    "roles": {role: body["total"] for role, body in roles.items()},
                }
            )
        del train, validation, predictor
    return result


def prepare_cohort(
    snapshot_path: Path,
    cohort_id: str,
    split: SplitPolicy,
    origin_window: OriginWindow,
    method_policy: FunctionalV12Policy,
    output: Path,
    *,
    feature_policy: FeaturePolicy | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    """Verify source once, preserve all compact rows, then fit local recipes without test reads."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", cohort_id):
        raise SnapshotError("functional_v12_invalid_cohort_id")
    if method_policy.mean_variant == "hgb_blend":
        raise SnapshotError("functional_v12_cohort_hgb_requires_explicit_pinned_model_provider")
    if output.resolve().is_relative_to(snapshot_path.resolve()):
        raise SnapshotError("functional_v12_cohort_output_inside_source")
    output.mkdir(parents=True, exist_ok=True)
    checked_directory(output)
    before_code = cohort_code()
    with tempfile.TemporaryDirectory(prefix=".cohort-verification-", dir=output) as seal_tmp:
        verified = seal_snapshot(snapshot_path, Path(seal_tmp) / "source_seal.json")
        with tempfile.TemporaryDirectory(prefix=".cohort-preparation-", dir=output) as tmp:
            root = Path(tmp)
            try:
                compact_root = build_compact_inputs(
                    verified,
                    root / "compact",
                    split,
                    origin_window=origin_window,
                    feature_policy=feature_policy,
                    progress=progress,
                )
                compact = load_compact_inputs(compact_root)
                normalized_seal = {k: v for k, v in verified.seal.items() if k != "verified_at"}
                write_private(root / "source_seal.json", canonical_bytes(normalized_seal) + b"\n")
                recipes = fit_local_recipes(
                    compact_root,
                    compact,
                    cohort_id,
                    method_policy,
                    root / "recipes",
                    progress=progress,
                )
                if cohort_code() != before_code:
                    raise SnapshotError("functional_v12_cohort_code_changed_during_fit")
                verified.verify_bytes()
                compact_relative = compact_root.relative_to(root).as_posix()
                descriptor = {
                    "version": VERSION,
                    "cohort_id": cohort_id,
                    "source_seal_id": normalized_seal["seal_id"],
                    "source_dataset_id": normalized_seal["descriptor"]["source_dataset_id"],
                    "snapshot_id": normalized_seal["descriptor"]["snapshot_id"],
                    "source_seal_file": _ref(root, "source_seal.json"),
                    "compact_parent": {
                        "path": compact_relative,
                        "inputs_id": compact["inputs_id"],
                        "manifest": _ref(root, compact_relative + "/compact_manifest.json"),
                        "full_source_verification": "passed",
                        "semantic_replay": "required_separate_campaign_receipt",
                    },
                    "split_policy": split.model_dump(mode="json"),
                    "origin_window": origin_window.model_dump(mode="json"),
                    "method_policy": method_policy.model_dump(mode="json"),
                    "code": before_code,
                    "recipes": recipes,
                    "complete_compact_counts": compact["descriptor"]["counts"],
                    "resource_limits": {"max_rows_per_role": MAX_ROWS_PER_ROLE},
                    "fitting_roles": list(DEVELOPMENT_ROLES),
                    "model_heads_fitted": [],
                    "holdout_iterator_opened_during_fit": False,
                    "holdout_metrics_evaluated": False,
                    "forecast_model_status": "not_ready",
                    "mean_pooling": "local_only_requires_all_cohorts_per_logical_fold",
                }
                manifest = {
                    "cohort_artifact_id": "forecast-cohort-sha256-" + canonical_sha256(descriptor),
                    "descriptor": descriptor,
                    "prepared_at": datetime.now(UTC).isoformat(),
                }
                write_private(root / "cohort_manifest.json", canonical_bytes(manifest) + b"\n")
                fsync_tree(root)
                destination = output / str(manifest["cohort_artifact_id"])
                if destination.exists():
                    if load_cohort(destination)["descriptor"] != descriptor:
                        raise SnapshotError("functional_v12_cohort_publication_conflict")
                else:
                    publish_noreplace(root, destination)
            except BaseException as exc:
                write_private(
                    root / "failure.json",
                    canonical_bytes(
                        {
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "cohort_id": cohort_id,
                            "forecast_model_status": "not_ready",
                            "partial_artifact_not_qualified": True,
                        }
                    )
                    + b"\n",
                )
                publish_noreplace(root, output / ("failed-" + root.name.lstrip(".")))
                raise
    return destination


def _ref(root: Path, name: str) -> dict[str, Any]:
    size, digest = file_hash(root, name)
    return {"path": name, "size_bytes": size, "sha256": digest}


def load_cohort(root: Path) -> dict[str, Any]:
    """Check immutable bytes and the current fitter snapshot, without fitting or prediction."""
    manifest = read_json(root, "cohort_manifest.json")
    desc = manifest["descriptor"]
    if (
        manifest["cohort_artifact_id"] != "forecast-cohort-sha256-" + canonical_sha256(desc)
        or desc["version"] != VERSION
        or desc["code"] != cohort_code()
        or desc["forecast_model_status"] != "not_ready"
        or desc["fitting_roles"] != list(DEVELOPMENT_ROLES)
        or desc["holdout_iterator_opened_during_fit"] is not False
        or desc["holdout_metrics_evaluated"] is not False
        or desc["model_heads_fitted"] != []
    ):
        raise SnapshotError("functional_v12_cohort_identity_code_or_scope")
    split = SplitPolicy.model_validate_json(canonical_bytes(desc["split_policy"]))
    policy = FunctionalV12Policy.model_validate_json(canonical_bytes(desc["method_policy"]))
    if set(desc["recipes"]) != {fold.name for fold in split.folds}:
        raise SnapshotError("functional_v12_cohort_fold_inventory")
    refs = [desc["source_seal_file"], desc["compact_parent"]["manifest"]]
    refs.extend(body["file"] for body in desc["recipes"].values())
    for ref in refs:
        if _ref(root, ref["path"]) != ref:
            raise SnapshotError("functional_v12_cohort_file_checksum")
    compact_path = desc["compact_parent"]["path"]
    compact = load_compact_inputs(root / compact_path)
    seal = read_json(root, "source_seal.json")
    if (
        compact["inputs_id"] != desc["compact_parent"]["inputs_id"]
        or compact["descriptor"]["source_seal"] != seal
        or seal["seal_id"] != desc["source_seal_id"]
        or seal["descriptor"]["source_dataset_id"] != desc["source_dataset_id"]
        or seal["descriptor"]["snapshot_id"] != desc["snapshot_id"]
        or compact["descriptor"]["split_policy"] != desc["split_policy"]
        or compact["descriptor"]["origin_window"] != desc["origin_window"]
        or compact["descriptor"]["counts"] != desc["complete_compact_counts"]
    ):
        raise SnapshotError("functional_v12_cohort_parent_binding")
    expected = {"cohort_manifest.json", *(ref["path"] for ref in refs)}
    expected.update(compact_path + "/" + name for name in compact["descriptor"]["files"])
    inventory(root, expected)
    for fold in split.folds:
        body = desc["recipes"][fold.name]
        recipe = read_json(root, body["file"]["path"])
        PreparedV12Predictor(recipe)
        if (
            recipe["recipe_id"] != body["recipe_id"]
            or recipe["fold"] != fold.name
            or recipe["policy"] != policy.model_dump(mode="json")
            or recipe["selection_cutoff"] != fold.selection_cutoff.isoformat()
            or recipe["support"]["cohort_ids"] != [desc["cohort_id"]]
            or recipe["training_support"]["cohort_ids"] != [desc["cohort_id"]]
            or set(body["roles"]) != set(DEVELOPMENT_ROLES)
        ):
            raise SnapshotError("functional_v12_cohort_recipe_binding")
    return manifest


def replay_cohort(root: Path) -> dict[str, Any]:
    """Re-predict saved local recipes on development rows; never refit or open holdout labels."""
    manifest = load_cohort(root)
    desc = manifest["descriptor"]
    compact_root = root / desc["compact_parent"]["path"]
    compact = load_compact_inputs(compact_root)
    results: dict[str, Any] = {}
    for fold, body in desc["recipes"].items():
        predictor = PreparedV12Predictor(read_json(root, body["file"]["path"]))
        results[fold] = {}
        for role in DEVELOPMENT_ROLES:
            rows, counts = _read_role(compact_root, compact, desc["cohort_id"], fold, role)
            receipt = counts | _prediction_receipt(rows, predictor)
            if receipt != body["roles"][role]:
                raise SnapshotError("functional_v12_cohort_prediction_replay_mismatch")
            results[fold][role] = receipt
    return {
        "status": "passed",
        "cohort_artifact_id": manifest["cohort_artifact_id"],
        "cohort_manifest_sha256": file_hash(root, "cohort_manifest.json")[1],
        "recipes": results,
        "fit_calls": 0,
        "roles_opened": list(DEVELOPMENT_ROLES),
        "compact_semantic_replay": "required_separate_campaign_receipt",
        "forecast_model_status": "not_ready",
    }
