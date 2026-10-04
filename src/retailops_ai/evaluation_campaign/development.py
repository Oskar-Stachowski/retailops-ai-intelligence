"""Sequential post-inventory development benchmark with immutable attempts and replay."""

import gc
import hashlib
import os
import time
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import numpy as np

from retailops_ai.curated.builder import verify_curated
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.comparison import require_same_forecast_keys
from retailops_ai.evaluation_campaign.development_contract import (
    COMPARISON_HEADS,
    MODELS,
    DevelopmentComparisonManifest,
    DevelopmentComparisonPolicy,
    DevelopmentPrediction,
    DevelopmentProtocol,
)
from retailops_ai.evaluation_campaign.development_inference import tensorflow_predictions
from retailops_ai.evaluation_campaign.development_metrics import (
    baseline_predictions,
    comparison_metrics,
)
from retailops_ai.evaluation_campaign.development_storage import development_parents
from retailops_ai.forecasting.contract import Parent
from retailops_ai.forecasting.functional_contract import FunctionalPipeline
from retailops_ai.forecasting.functional_models import fit_worker, functional_code
from retailops_ai.forecasting.functional_preprocessing import (
    fit_ordered_train_samples,
    transform_values,
)
from retailops_ai.forecasting.manifest_contract import (
    FeatureManifest,
    FeaturePolicy,
    FoldPlan,
    SplitManifest,
)
from retailops_ai.forecasting.manifests import feature_key
from retailops_ai.forecasting.model_contract import MAX_MODEL_BYTES, ResourceReceipt
from retailops_ai.forecasting.model_trees import TreePredictor
from retailops_ai.forecasting.models import model_code
from retailops_ai.forecasting.preprocessing import FittedState
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    read_bytes,
    read_json,
)
from retailops_ai.source_snapshot.publish import fsync_tree
from retailops_ai.tensorflow_challenger.contract import ChallengerManifest
from retailops_ai.tensorflow_challenger.dataset import Sample, Window, fit_normalization
from retailops_ai.tensorflow_challenger.pipeline import (
    environment_lock,
    fit_challenger,
    implementation,
    verify_artifact,
)

DEFAULT_COMPARISON_POLICY = DevelopmentComparisonPolicy()


def comparison_code() -> str:
    own = {
        name: hashlib.sha256(
            files("retailops_ai.evaluation_campaign").joinpath(name).read_bytes()
        ).hexdigest()
        for name in (
            "development.py",
            "development_contract.py",
            "development_metrics.py",
            "development_storage.py",
            "development_inference.py",
            "comparison.py",
        )
    }
    return canonical_sha256(
        {"comparison": own, "trees": functional_code(), "tensorflow": implementation()}
    )


def _write(path: Path, value: Any) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_bytes(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _event(root: Path, event: str, **details: Any) -> None:
    with (root / "trials.jsonl").open("ab") as stream:
        stream.write(
            canonical_bytes({"event": event, "at": datetime.now(UTC).isoformat(), **details})
            + b"\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


def _attempt(root: Path, status: str, **details: Any) -> None:
    temporary = root / ".attempt-new.json"
    _write(
        temporary,
        {"status": status, "final_test_accessed": False, "promotion_allowed": False, **details},
    )
    temporary.replace(root / "attempt.json")


def _protocol(
    curated: Path,
    feature: FeatureManifest,
    split: SplitManifest,
    fold: FoldPlan,
    train: tuple[Window, ...],
    validation: tuple[Window, ...],
    policy: DevelopmentComparisonPolicy,
) -> DevelopmentProtocol:
    document = verify_curated(curated)
    descriptor = document["descriptor"]
    if document["schema_version"] != "1.1.0" or descriptor["source_schema_version"] != "2.7.0":
        raise SnapshotError("development_comparison_requires_post_inventory_curated")
    parent = Parent(
        source_dataset_id=descriptor["parent_source_dataset_id"],
        curated_dataset_id=document["curated_dataset_id"],
        snapshot_id=descriptor["parent_snapshot_id"],
        curated_descriptor_sha256=canonical_sha256(descriptor),
        business_timezone=descriptor["config"]["business_timezone"],
        forecast_source_status=document["readiness"]["forecast_source"],
    )
    if (
        parent != feature.descriptor.parent
        or descriptor["source_parameters"] != feature.descriptor.source_parameters
    ):
        raise SnapshotError("development_comparison_curated_parent_mismatch")
    if feature.descriptor.source_parameters.get("seed") != policy.tensorflow.data_seed:
        raise SnapshotError("development_comparison_data_seed_mismatch")
    if feature.descriptor.resolved_policy != FeaturePolicy():
        raise SnapshotError("development_comparison_requires_supported_feature_policy")
    populations = []
    for windows in (train, validation):
        rows = [s.row for w in windows for s in w.samples]
        populations.append(require_same_forecast_keys(rows, rows)[1])
    return DevelopmentProtocol(
        policy=policy,
        feature_set_id=feature.feature_set_id,
        split_id=split.split_id,
        parent=parent,
        source_parameters=feature.descriptor.source_parameters,
        source_schema_version=descriptor["source_schema_version"],
        fold=fold,
        feature_descriptor_sha256=canonical_sha256(feature.descriptor.model_dump(mode="json")),
        split_descriptor_sha256=canonical_sha256(split.descriptor.model_dump(mode="json")),
        train_population_sha256=populations[0],
        validation_population_sha256=populations[1],
        core_environment=model_code().model_dump(mode="json"),
        tensorflow_lock_sha256=hashlib.sha256(environment_lock()).hexdigest(),
        implementation_sha256=comparison_code(),
    )


def _tree_state(
    train: tuple[Window, ...], protocol: DevelopmentProtocol
) -> tuple[list[Sample], FittedState, str]:
    eligible = sorted(
        (s for w in train for s in w.samples if s.membership.eligible),
        key=lambda s: feature_key(s.row),
    )
    state = fit_ordered_train_samples(
        ((s.row, s.membership) for s in eligible),
        fold=protocol.fold,
        policy=FeaturePolicy(),
        feature_set_id=protocol.feature_set_id,
        split_id=protocol.split_id,
    )
    rows, columns = len(eligible), len(state.descriptor.output_columns) + 1
    policy = protocol.policy.trees.model
    if rows > policy.max_train_rows or rows * (columns + 1) * 8 > policy.max_matrix_bytes:
        raise SnapshotError("development_comparison_tree_matrix_budget")
    digest = hashlib.sha256()
    for sample in eligible:
        digest.update(canonical_bytes(sample.label.model_dump(mode="json")) + b"\n")
    return eligible, state, digest.hexdigest()


def _trees(
    root: Path, train: tuple[Window, ...], protocol: DevelopmentProtocol, *, replay: bool
) -> dict[str, FunctionalPipeline]:
    eligible, state, label_digest = _tree_state(train, protocol)
    output: dict[str, FunctionalPipeline] = {}
    x = y = None
    if not replay:
        x = np.asarray(
            [
                (
                    *transform_values({v.name: v.value for v in s.row.values}, state),
                    float(s.row.horizon_days),
                )
                for s in eligible
            ],
            dtype=np.float64,
        )
        y = np.asarray([s.label.observed_sales_units for s in eligible], dtype=np.float64)
        (root / "trees").mkdir(mode=0o700)
    for head in COMPARISON_HEADS:
        payload = {
            "schema_version": "2.0.0",
            "head": head,
            "policy": protocol.policy.trees.model_dump(mode="json"),
            "preprocessing": state.model_dump(mode="json"),
            "train_labels_sha256": label_digest,
            "code_sha256": canonical_sha256(functional_code()),
        }
        path = root / "trees" / (head + ".json")
        if replay:
            pipeline = FunctionalPipeline.model_validate_json(
                read_bytes(root, "trees/" + head + ".json", MAX_MODEL_BYTES)
            )
            if (
                any(
                    getattr(pipeline, key) != payload[key]
                    for key in ("head", "train_labels_sha256", "code_sha256")
                )
                or pipeline.policy != protocol.policy.trees
                or pipeline.preprocessing.descriptor != state.descriptor
            ):
                raise SnapshotError("development_comparison_tree_binding_mismatch")
            measured = read_json(root, "trees/" + head + ".resources.json")
            train_rows = measured.pop("train_rows")
            resources = ResourceReceipt.model_validate_json(canonical_bytes(measured))
            budget = protocol.policy.trees.model
            if (
                train_rows != len(eligible)
                or resources.wall_seconds > budget.fit_wall_seconds
                or resources.cpu_seconds > budget.fit_cpu_seconds
                or resources.peak_rss_bytes > budget.fit_rss_bytes
            ):
                raise SnapshotError("development_comparison_tree_resource_budget")
        else:
            _event(
                root,
                "started",
                model=head,
                configuration=protocol.policy.trees.model.model_dump(mode="json"),
            )
            if x is None or y is None:
                raise SnapshotError("development_comparison_missing_training_matrix")
            estimator, receipt = fit_worker(x, y, head, protocol.policy.trees)
            payload["estimator"] = estimator.model_dump(mode="json")
            identity = payload | {"preprocessing": state.descriptor.model_dump(mode="json")}
            pipeline = FunctionalPipeline.model_validate_json(
                canonical_bytes(
                    payload
                    | {
                        "model_id": "model-sha256-" + canonical_sha256(identity),
                        "generated_at": datetime.now(UTC).isoformat(),
                    }
                )
            )
            _write(path, pipeline.model_dump(mode="json"))
            _write(
                path.with_suffix(".resources.json"),
                receipt.model_dump(mode="json") | {"train_rows": len(eligible)},
            )
            _event(root, "completed", model=head, model_id=pipeline.model_id)
        output[head] = pipeline
    return output


def _predictions(
    root: Path,
    validation: tuple[Window, ...],
    trees: dict[str, FunctionalPipeline],
    *,
    inference_resources: dict[str, Any] | None = None,
    tensorflow_rows: tuple[DevelopmentPrediction, ...] | None = None,
) -> dict[str, tuple[DevelopmentPrediction, ...]]:
    if tensorflow_rows is None:
        tensorflow_rows, measured = tensorflow_predictions(root / "tensorflow", validation)
        if inference_resources is not None:
            inference_resources.update(measured)
    output = baseline_predictions(validation)
    state = trees["rf_mean"].preprocessing
    predictors = {name: TreePredictor(p.estimator) for name, p in trees.items()}
    samples = [s for w in validation for s in w.samples]
    tree_rows: dict[str, list[DevelopmentPrediction]] = {"rf_mean": [], "hgb": []}
    for start in range(0, len(samples), 256):
        batch = samples[start : start + 256]
        x = np.asarray(
            [
                (
                    *transform_values({v.name: v.value for v in s.row.values}, state),
                    float(s.row.horizon_days),
                )
                for s in batch
            ],
            dtype=np.float64,
        )
        values = {name: p.matrix(x) for name, p in predictors.items()}
        for index, sample in enumerate(batch):
            key = sample.row.model_dump(include=set(DevelopmentPrediction.model_fields))
            eligible = sample.membership.eligible
            tree_rows["rf_mean"].append(
                DevelopmentPrediction(
                    **key,
                    value=FunctionalForecast(
                        mean=float(values["rf_mean"][index]) if eligible else None,
                        median=None,
                        interval=None,
                    ),
                )
            )
            tree_rows["hgb"].append(
                DevelopmentPrediction(
                    **key,
                    value=FunctionalForecast(
                        mean=float(values["hgb_mean"][index]) if eligible else None,
                        median=float(values["hgb_median"][index]) if eligible else None,
                        interval=None,
                    ),
                )
            )
    output.update({name: tuple(rows) for name, rows in tree_rows.items()})
    output["tensorflow"] = tensorflow_rows
    return output


def _prediction_bytes(predictions: dict[str, tuple[DevelopmentPrediction, ...]]) -> bytes:
    return b"".join(
        canonical_bytes({"model": name, **p.model_dump(mode="json")}) + b"\n"
        for name in MODELS
        for p in predictions[name]
    )


def _inventory(root: Path, limit: int) -> dict[str, dict[str, int | str]]:
    result: dict[str, dict[str, int | str]] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SnapshotError("development_comparison_symlink")
        if not path.is_file() or path.name == "manifest.json" and path.parent == root:
            continue
        name = path.relative_to(root).as_posix()
        size, digest = file_hash(root, name)
        total += size
        if total > limit or len(result) >= 10000:
            raise SnapshotError("development_comparison_output_budget")
        result[name] = {"size_bytes": size, "sha256": digest}
    return result


def run_development_comparison(
    *,
    features: Path,
    split: Path,
    curated: Path,
    fold_name: str,
    output: Path,
    policy: DevelopmentComparisonPolicy = DEFAULT_COMPARISON_POLICY,
) -> dict[str, Any]:
    output = output.absolute()
    if any(output.is_relative_to(p.resolve()) for p in (features, split, curated)):
        raise SnapshotError("development_comparison_output_inside_parent")
    checked_directory(output.parent)
    with development_parents(features, split, fold_name, policy.tensorflow.max_windows) as parents:
        feature, split_manifest, fold, train, validation = parents
        protocol = _protocol(curated, feature, split_manifest, fold, train, validation, policy)
        _tree_state(
            train, protocol
        )  # Refuse the matrix budget before allocating or creating an attempt.
        output.mkdir(mode=0o700)
        _write(output / "protocol.json", protocol.model_dump(mode="json"))
        _attempt(output, "running")
        _event(
            output,
            "protocol_frozen",
            protocol_sha256=canonical_sha256(protocol.model_dump(mode="json")),
        )
        try:
            # Large portable forests need not remain resident beside the TF worker.
            _trees(output, train, protocol, replay=False)
            gc.collect()
            _event(
                output,
                "started",
                model="tensorflow",
                configuration=policy.tensorflow.model_dump(mode="json"),
            )
            tf = fit_challenger(
                train,
                validation,
                fold=fold,
                feature_set_id=feature.feature_set_id,
                split_id=split_manifest.split_id,
                output=output / "tensorflow",
                policy=policy.tensorflow,
            )
            _event(output, "completed", model="tensorflow", model_id=tf["model_id"])
            started = time.monotonic()
            # Complete the framework process before reloading the large portable forest.
            tf_rows, inference = tensorflow_predictions(output / "tensorflow", validation)
            trees = _trees(output, train, protocol, replay=True)
            predictions = _predictions(output, validation, trees, tensorflow_rows=tf_rows)
            inference_seconds = time.monotonic() - started
            report = comparison_metrics(validation, predictions, train)
            report["model_ids"] = {head: p.model_id for head, p in trees.items()} | {
                "tensorflow": tf["model_id"]
            }
            _write(output / "report.json", report)
            raw = _prediction_bytes(predictions)
            if len(raw) > policy.max_output_bytes:
                raise SnapshotError("development_comparison_output_budget")
            with (output / "predictions.jsonl").open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _write(
                output / "resources.json",
                {
                    "validation_rows": report["prediction_rows_per_model"],
                    "all_models_reload_and_prediction_seconds": inference_seconds,
                    "tensorflow": {
                        "inference": inference,
                        "worker": tf["worker"],
                        "resources": tf["resources"],
                        "artifact_bytes": tf["artifact_bytes"],
                    },
                    "scope": "sequential_model_workers_not_a_full_pipeline_peak_rss_receipt",
                },
            )
            _event(output, "comparison_completed", deployment_status="not_ready")
            _attempt(output, "completed_development_diagnostic")
            inventory = _inventory(output, policy.max_output_bytes)
            identity = {"protocol": protocol.model_dump(mode="json"), "files": inventory}
            manifest = DevelopmentComparisonManifest.model_validate_json(
                canonical_bytes(
                    identity
                    | {
                        "comparison_id": "ai09-development-sha256-" + canonical_sha256(identity),
                        "status": "completed_development_diagnostic",
                    }
                )
            )
            _write(output / "manifest.json", manifest.model_dump(mode="json"))
            fsync_tree(output)
            return manifest.model_dump(mode="json")
        except Exception as exc:
            _event(
                output,
                "failed",
                error_code=str(exc) if isinstance(exc, SnapshotError) else type(exc).__name__,
            )
            _attempt(
                output,
                "failed",
                error_code=str(exc) if isinstance(exc, SnapshotError) else type(exc).__name__,
            )
            raise


def verify_development_comparison(
    *,
    output: Path,
    features: Path,
    split: Path,
    curated: Path,
) -> dict[str, Any]:
    output = checked_directory(output)
    manifest = DevelopmentComparisonManifest.model_validate_json(
        read_bytes(output, "manifest.json")
    )
    protocol = manifest.protocol
    if _inventory(output, protocol.policy.max_output_bytes) != {
        name: f.model_dump() for name, f in manifest.files.items()
    }:
        raise SnapshotError("development_comparison_inventory_mismatch")
    if (
        read_json(output, "protocol.json") != protocol.model_dump(mode="json")
        or read_json(output, "attempt.json")["status"] != "completed_development_diagnostic"
    ):
        raise SnapshotError("development_comparison_protocol_or_attempt_mismatch")
    with development_parents(
        features, split, protocol.fold.name, protocol.policy.tensorflow.max_windows
    ) as parents:
        feature, split_manifest, fold, train, validation = parents
        expected = _protocol(
            curated, feature, split_manifest, fold, train, validation, protocol.policy
        )
        if expected != protocol:
            raise SnapshotError("development_comparison_parent_code_or_environment_mismatch")
        tf, state = verify_artifact(output / "tensorflow")
        checked = ChallengerManifest.model_validate_json(canonical_bytes(tf))
        if (
            checked.descriptor.binding.feature_set_id != protocol.feature_set_id
            or checked.descriptor.binding.split_id != protocol.split_id
            or checked.descriptor.policy != protocol.policy.tensorflow
            or state.content_sha256()
            != fit_normalization(
                train, fold=fold, feature_set_id=protocol.feature_set_id, split_id=protocol.split_id
            ).content_sha256()
        ):
            raise SnapshotError("development_comparison_tensorflow_parent_mismatch")
        # Inventory is already sealed. Validate/reload every tree after the TF worker exits,
        # before any metric or replay result can be accepted.
        tf_rows, _ = tensorflow_predictions(output / "tensorflow", validation)
        trees = _trees(output, train, protocol, replay=True)
        predictions = _predictions(output, validation, trees, tensorflow_rows=tf_rows)
        if read_bytes(
            output, "predictions.jsonl", protocol.policy.max_output_bytes
        ) != _prediction_bytes(predictions):
            raise SnapshotError("development_comparison_prediction_replay_mismatch")
        report = comparison_metrics(validation, predictions, train)
        report["model_ids"] = {head: p.model_id for head, p in trees.items()} | {
            "tensorflow": tf["model_id"]
        }
        if report != read_json(output, "report.json"):
            raise SnapshotError("development_comparison_metric_replay_mismatch")
    return manifest.model_dump(mode="json")
