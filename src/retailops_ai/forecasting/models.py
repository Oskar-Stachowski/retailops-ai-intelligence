"""Train-only learned pipelines; comparison uses the baseline evaluator's common-key engine."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from importlib import metadata
from importlib.resources import files
from pathlib import Path
from typing import Any

import numpy as np
import psutil  # type: ignore[import-untyped]
from numpy.typing import NDArray

import retailops_ai
from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.evaluation import (
    MAX_LINE,
    MAX_PREDICTIONS,
    _database,
    defer_holdout,
    evaluation_code,
    score_model,
    select_validation,
)
from retailops_ai.forecasting.evaluation_contract import BaselinePrediction, MetricResult
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifest_contract import (
    FileReceipt,
    LabelPoint,
    Membership,
    TableReceipt,
)
from retailops_ai.forecasting.manifest_io import MAX_BYTES, dump, key
from retailops_ai.forecasting.manifests import feature_key, input_models, load_feature_set
from retailops_ai.forecasting.model_contract import (
    MAX_MODEL_BYTES,
    LearnedEstimator,
    LearnedName,
    ModelCode,
    ModelName,
    ModelPipeline,
    ModelPolicy,
    ModelPrediction,
    ModelRunDescriptor,
    ModelRunManifest,
    ModelSelection,
    PipelineDescriptor,
    ResourceReceipt,
)
from retailops_ai.forecasting.model_trees import ForecastAdapter
from retailops_ai.forecasting.preprocessing import fit_train_samples, transform
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_bytes,
    read_json,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

LEARNED: tuple[LearnedName, ...] = ("random_forest", "hist_gradient_boosting")


def model_code() -> ModelCode:
    base = evaluation_code()
    hashes = {
        **base.code_files,
        **{
            "forecasting/" + name: hashlib.sha256(
                files("retailops_ai.forecasting").joinpath(name).read_bytes()
            ).hexdigest()
            for name in ("model_contract.py", "model_trees.py", "model_worker.py", "models.py")
        },
    }
    versions = {
        name: metadata.version(name)
        for name in ("scikit-learn", "numpy", "scipy", "joblib", "threadpoolctl", "psutil")
    }
    versions.update(python=base.python_version, pyarrow=base.pyarrow_version)
    return ModelCode(
        code_files=hashes,
        code_sha256=canonical_sha256(hashes),
        dependency_lock_sha256=base.dependency_lock_sha256,
        versions=versions,
        system=platform.system(),
        machine=platform.machine(),
    )


def fit_worker(
    x: NDArray[np.float64], y: NDArray[np.float64], family: LearnedName, policy: ModelPolicy
) -> tuple[LearnedEstimator, ResourceReceipt]:
    if (
        x.ndim != 2
        or y.shape != (x.shape[0],)
        or x.shape[0] > policy.max_train_rows
        or x.nbytes + y.nbytes > policy.max_matrix_bytes
    ):
        raise SnapshotError("forecast_model_training_matrix_budget")
    with tempfile.TemporaryDirectory(prefix="forecast-fit-") as temporary:
        root = Path(temporary).resolve()
        np.save(root / "x.npy", x, allow_pickle=False)
        np.save(root / "y.npy", y, allow_pickle=False)
        (root / "job.json").write_text(
            json.dumps({"family": family, "policy": policy.model_dump(mode="json")})
        )
        environment = {
            **os.environ,
            **{
                name: "1"
                for name in (
                    "OMP_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS",
                    "MKL_NUM_THREADS",
                    "BLIS_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS",
                    "NUMEXPR_NUM_THREADS",
                    "LOKY_MAX_CPU_COUNT",
                )
            },
        }
        environment["PYTHONPATH"] = str(Path(retailops_ai.__file__).parent.parent)
        started = time.monotonic()
        peak, cpu = 0, 0.0
        process = subprocess.Popen(  # noqa: S603 -- fixed module, interpreter and private numeric job
            [sys.executable, "-m", "retailops_ai.forecasting.model_worker", str(root)],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )  # noqa: S603 -- fixed interpreter/module and private numeric job
        monitor = psutil.Process(process.pid)
        try:
            while process.poll() is None:
                if time.monotonic() - started > policy.fit_wall_seconds:
                    raise SnapshotError("forecast_model_fit_wall_budget_exceeded")
                try:
                    peak = max(peak, monitor.memory_info().rss)
                    times = monitor.cpu_times()
                    cpu = max(cpu, times.user + times.system)
                except psutil.NoSuchProcess:
                    continue
                except psutil.Error as exc:
                    raise SnapshotError("forecast_model_resource_monitor_unavailable") from exc
                if peak > policy.fit_rss_bytes or cpu > policy.fit_cpu_seconds:
                    raise SnapshotError("forecast_model_fit_resource_budget_exceeded")
                time.sleep(policy.monitor_interval_seconds)
            if process.returncode != 0:
                raise SnapshotError("forecast_model_fit_worker_failed")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        receipt = read_json(root, "worker_receipt.json")
        peak, cpu = max(peak, receipt["peak_rss_bytes"]), max(cpu, receipt["cpu_seconds"])
        elapsed = time.monotonic() - started
        if (
            peak > policy.fit_rss_bytes
            or cpu > policy.fit_cpu_seconds
            or elapsed > policy.fit_wall_seconds
        ):
            raise SnapshotError("forecast_model_fit_final_budget_exceeded")
        raw = read_bytes(root, "estimator.json", MAX_MODEL_BYTES)
        decode_json(raw)
        estimator = LearnedEstimator.model_validate_json(raw)
        if estimator.family != family or estimator.feature_count != x.shape[1]:
            raise SnapshotError("forecast_worker_output_binding_mismatch")
        return estimator, ResourceReceipt(
            wall_seconds=elapsed, cpu_seconds=cpu, peak_rss_bytes=peak
        )


def _fit_fold(
    db: sqlite3.Connection,
    feature_dir: Path,
    split: Any,
    fold: Any,
    policy: ModelPolicy,
    code: ModelCode,
) -> dict[LearnedName, tuple[ModelPipeline, ResourceReceipt]]:
    members: dict[bytes, tuple[Membership, LabelPoint]] = {}
    for member_body, label_body in db.execute(
        "SELECT m.body,l.body FROM members m JOIN predictions p ON p.key=m.key LEFT JOIN labels l ON l.key=m.key WHERE p.model='last_observed' AND p.fold=? AND p.role='train' AND p.eligible=1",
        (fold.name,),
    ):
        member = Membership.model_validate_json(member_body)
        label = LabelPoint.model_validate_json(label_body)
        if (
            label.role != "train"
            or label.fold != fold.name
            or label.status != "eligible"
            or label.observed_sales_units is None
            or label.label_available_at is None
            or label.label_available_at > fold.training_cutoff
        ):
            raise SnapshotError("forecast_model_fit_requires_mature_fold_train_labels")
        members[feature_key(member)] = member, label
        if len(members) > policy.max_train_rows:
            raise SnapshotError("forecast_model_training_row_budget")
    samples = []
    size = 0
    for row in input_models(feature_dir, "features"):
        if isinstance(row, InputRow) and feature_key(row) in members:
            size += len(canonical_bytes(row.model_dump(mode="json")))
            if size > 128 * 1024**2:
                raise SnapshotError("forecast_model_raw_training_budget")
            samples.append(row)
    samples.sort(key=feature_key)
    if len(samples) != len(members):
        raise SnapshotError("forecast_model_missing_train_feature_keys")
    features = load_feature_set(feature_dir)
    state = fit_train_samples(
        ((row, members[feature_key(row)][0]) for row in samples),
        fold=fold,
        policy=features.descriptor.resolved_policy,
        feature_set_id=features.feature_set_id,
        split_id=split.split_id,
    )
    dimensions = len(state.descriptor.output_columns) + 1
    if len(samples) * (dimensions + 1) * 8 > policy.max_matrix_bytes:
        raise SnapshotError("forecast_model_training_matrix_budget")
    x = np.empty((len(samples), dimensions), dtype=np.float64)
    y = np.empty(len(samples), dtype=np.float64)
    digest = hashlib.sha256()
    for index, row in enumerate(samples):
        x[index] = (
            *transform(row, state, feature_set_id=features.feature_set_id),
            float(row.horizon_days),
        )
        label = members[feature_key(row)][1]
        y[index] = label.observed_sales_units
        digest.update(canonical_bytes(label.model_dump(mode="json")) + b"\n")
    fitted = {}
    for family in LEARNED:
        estimator, resources = fit_worker(x, y, family, policy)
        descriptor = PipelineDescriptor(
            family=family,
            feature_set_id=features.feature_set_id,
            split_id=split.split_id,
            policy=policy,
            preprocessing=state.descriptor,
            train_labels_content_sha256=digest.hexdigest(),
            output_columns=(*state.descriptor.output_columns, "horizon_days"),
            estimator=estimator,
            code=code,
        )
        pipeline = ModelPipeline(
            model_id="model-sha256-" + canonical_sha256(descriptor.model_dump(mode="json")),
            descriptor=descriptor,
            generated_at=datetime.now(UTC),
        )
        fitted[family] = pipeline, resources
    return fitted


def compare_validation(
    policy: ModelPolicy,
    metrics: dict[str, MetricResult],
    grain_sha256: str,
    *,
    fold: str,
    feature_set_id: str,
    split_id: str,
) -> ModelSelection:
    names = (*policy.baseline.candidates, *LEARNED)
    if (
        set(metrics) != set(names)
        or len({metric.eligible_rows for metric in metrics.values()}) != 1
    ):
        raise SnapshotError("forecast_model_validation_common_keys_mismatch")
    baseline_metrics = {name: metrics[name] for name in policy.baseline.candidates}
    baseline = select_validation(
        policy.baseline,
        baseline_metrics,
        grain_sha256,
        fold=fold,
        feature_set_id=feature_set_id,
        split_id=split_id,
    )
    selected: ModelName | None = None
    if baseline.model is not None and all(metric.status == "passed" for metric in metrics.values()):
        selected = baseline.model
        baseline_mae = metrics[baseline.model].mae
        if baseline_mae is None:
            raise SnapshotError("forecast_model_baseline_metric_missing")
        best = sorted(LEARNED, key=lambda name: (metrics[name].mae, LEARNED.index(name)))[0]
        candidate_mae = metrics[best].mae
        if candidate_mae is not None and candidate_mae < baseline_mae * (
            1 - policy.minimum_relative_improvement
        ):
            selected = best
    body = {
        "policy": policy.model_dump(mode="json"),
        "fold": fold,
        "feature_set_id": feature_set_id,
        "split_id": split_id,
        "validation_grain_sha256": grain_sha256,
        "baseline": baseline.model,
        "selected": selected,
        "metrics": {name: value.model_dump(mode="json") for name, value in metrics.items()},
    }
    return ModelSelection(
        fold=fold,
        baseline=baseline.model,
        selected=selected,
        status="selected" if selected else "not_ready",
        validation_grain_sha256=grain_sha256,
        validation_metrics=metrics,
        selection_sha256=canonical_sha256(body),
    )


def _predictions(
    db: sqlite3.Connection,
    feature_dir: Path,
    pipelines: dict[str, ModelPipeline],
    coverage: Counter[str],
) -> None:
    adapters = {name: ForecastAdapter(pipeline) for name, pipeline in pipelines.items()}
    batches: dict[tuple[str, str], list[tuple[InputRow, Membership]]] = {}
    feature_id = load_feature_set(feature_dir).feature_set_id

    def insert(
        row: InputRow,
        member: Membership,
        family: LearnedName,
        pipeline: ModelPipeline,
        units: float | None,
    ) -> None:
        prediction = ModelPrediction(
            **row.model_dump(include=set(ForecastKey.model_fields)),
            fold=member.fold,
            role=member.role,
            model=family,
            model_id=pipeline.model_id,
            eligible=member.eligible,
            exclusion_reasons=member.reasons,
            predicted_units=units,
            prediction_kind="in_sample_diagnostic"
            if member.eligible and member.role == "train"
            else "out_of_time_diagnostic"
            if member.eligible
            else "excluded",
            training_knowledge_cutoff=pipeline.descriptor.preprocessing.fold.training_cutoff,
        )
        body = canonical_bytes(prediction.model_dump(mode="json"))
        if len(body) + 1 > MAX_LINE:
            raise SnapshotError("forecast_model_prediction_line_limit")
        db.execute(
            "INSERT INTO predictions VALUES (?,?,?,?,?,?,?)",
            (key(member), family, member.fold, member.role, member.eligible, units, body),
        )
        if member.eligible:
            coverage[member.fold + ":" + member.role + ":" + family + ":predicted"] += 1

    def flush(fold: str, role: str) -> None:
        batch = batches[(fold, role)]
        if not batch:
            return
        for family in LEARNED:
            adapter = adapters[fold + ":" + family]
            quantities = (
                adapter.diagnose_train([row for row, _ in batch], feature_set_id=feature_id)
                if role == "train"
                else adapter.predict([row for row, _ in batch], feature_set_id=feature_id)
            )
            for (row, member), units in zip(batch, quantities, strict=True):
                insert(row, member, family, adapter.pipeline, units)
        batch.clear()

    for row in input_models(feature_dir, "features"):
        if not isinstance(row, InputRow):
            raise SnapshotError("forecast_model_input_schema_mismatch")
        for (body,) in db.execute(
            "SELECT body FROM members WHERE feature_key=?", (feature_key(row),)
        ):
            member = Membership.model_validate_json(body)
            if not member.eligible:
                for family in LEARNED:
                    insert(row, member, family, pipelines[member.fold + ":" + family], None)
                continue
            batch = batches.setdefault((member.fold, member.role), [])
            batch.append((row, member))
            if len(batch) == 256:
                flush(member.fold, member.role)
    for fold, role in batches:
        flush(fold, role)


def model_card(manifest: ModelRunManifest) -> dict[str, Any]:
    descriptor = manifest.descriptor
    return {
        "comparison_id": manifest.comparison_id,
        "parent": descriptor.parent.model_dump(mode="json"),
        "feature_set_id": descriptor.feature_set_id,
        "split_id": descriptor.split_id,
        "target_type": "observed_sales_units",
        "inventory_features": "excluded_before_AI_06_acceptance",
        "strategy": descriptor.policy.strategy,
        "quality_status": "not_ready_pending_backtest_segment_gates_and_lifecycle",
        "selection": [selection.model_dump(mode="json") for selection in descriptor.selections],
        "legacy_retailops_serving": descriptor.policy.legacy_retailops_serving,
        "limitations": [
            "observed sales can be limited by inventory; this is not unconstrained demand",
            "synthetic temporal smoke is protocol evidence, not commercial quality",
            "train scores are in-sample diagnostics and cannot become historical serving features",
            "no portfolio final test, promotion, API serving or AWS deployment",
        ],
    }


def _report(
    root: Path, db: sqlite3.Connection, feature_dir: Path, split_dir: Path, policy: ModelPolicy
) -> ModelRunManifest:
    code = model_code()
    if code.versions["scikit-learn"] != "1.9.1":
        raise SnapshotError("forecast_model_unqualified_sklearn_version")
    split, coverage = _database(db, feature_dir, split_dir, policy.baseline)
    count = split.tables["memberships"].row_count * 5
    if count > MAX_PREDICTIONS:
        raise SnapshotError("forecast_model_prediction_resource_limit")
    pipelines, receipts, resources = {}, {}, {}
    (root / "models").mkdir()
    for index, fold in enumerate(split.descriptor.resolved_policy.folds):
        fitted = _fit_fold(db, feature_dir, split, fold, policy, code)
        for family, (pipeline, resource_receipt) in fitted.items():
            name = fold.name + ":" + family
            pipelines[name] = pipeline
            path = f"models/{index:02d}-{family}.json"
            raw = pipeline.model_dump_json().encode()
            if len(raw) > MAX_MODEL_BYTES:
                raise SnapshotError("forecast_model_pipeline_file_limit")
            (root / path).write_bytes(raw)
            size, pipeline_sha = file_hash(root, path)
            receipts[name] = FileReceipt(
                path=path, size_bytes=size, sha256=pipeline_sha, row_count=1
            )
            resources[name] = resource_receipt
    _predictions(db, feature_dir, pipelines, coverage)
    metrics: dict[str, MetricResult] = {}
    selections = []
    names = (*policy.baseline.candidates, *LEARNED)
    for fold in split.descriptor.resolved_policy.folds:
        validation: dict[str, MetricResult] = {
            name: score_model(db, fold.name, "validation", name) for name in names
        }
        grain = hashlib.sha256()
        for (row_key,) in db.execute(
            "SELECT DISTINCT key FROM predictions WHERE fold=? AND role='validation' AND eligible=1 ORDER BY key",
            (fold.name,),
        ):
            grain.update(row_key + b"\n")
        selection = compare_validation(
            policy,
            validation,
            grain.hexdigest(),
            fold=fold.name,
            feature_set_id=split.descriptor.feature_set_id,
            split_id=split.split_id,
        )
        selections.append(selection)
        for role in ("train", "validation", "development_holdout"):
            for name in names:
                metrics[fold.name + ":" + role + ":" + name] = (
                    validation[name]
                    if role == "validation"
                    else defer_holdout(db, fold.name, name)
                    if role == "development_holdout" and selection.selected is None
                    else score_model(db, fold.name, role, name)
                )
    digest = hashlib.sha256()
    rows = size = 0
    with (root / "predictions.jsonl").open("xb") as stream:
        for (body,) in db.execute("SELECT body FROM predictions ORDER BY key,model"):
            line = body + b"\n"
            rows += 1
            size += len(line)
            if rows > MAX_PREDICTIONS or size > MAX_BYTES:
                raise SnapshotError("forecast_model_prediction_resource_limit")
            stream.write(line)
            digest.update(line)
    if rows != count:
        raise SnapshotError("forecast_model_common_prediction_key_count_mismatch")
    receipt = TableReceipt(
        content_sha256=digest.hexdigest(),
        row_count=rows,
        files=(
            FileReceipt(
                path="predictions.jsonl", size_bytes=size, sha256=digest.hexdigest(), row_count=rows
            ),
        ),
    )
    descriptor = ModelRunDescriptor(
        feature_set_id=split.descriptor.feature_set_id,
        split_id=split.split_id,
        parent=split.descriptor.parent,
        label_dataset_id=split.descriptor.label_dataset_id,
        policy=policy,
        code=code,
        models={name: pipeline.model_id for name, pipeline in pipelines.items()},
        predictions_content_sha256=receipt.content_sha256,
        prediction_rows=rows,
        coverage_counts=dict(coverage),
        selections=tuple(selections),
        metrics=metrics,
        status="passed"
        if all(selection.status == "selected" for selection in selections)
        and all(metric.status == "passed" for metric in metrics.values())
        else "not_ready",
    )
    manifest = ModelRunManifest(
        comparison_id="forecast-model-comparison-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
        predictions=receipt,
        pipelines=receipts,
        resources=resources,
        generated_at=datetime.now(UTC),
    )
    if code != model_code():
        raise SnapshotError("forecast_model_implementation_changed_during_run")
    dump(root / "comparison_manifest.json", manifest)
    (root / "model_card.json").write_bytes(canonical_bytes(model_card(manifest)) + b"\n")
    return manifest


def load_comparison(root: Path) -> ModelRunManifest:
    checked_directory(root)
    raw = read_bytes(root, "comparison_manifest.json")
    decode_json(raw)
    manifest = ModelRunManifest.model_validate_json(raw)
    expected = {"comparison_manifest.json", "predictions.jsonl", "model_card.json"}
    loaded_pipelines = {}
    for name, receipt in manifest.pipelines.items():
        with regular_file(root, receipt.path) as stream:
            stream.seek(0, 2)
            if stream.tell() != receipt.size_bytes or stream.tell() > MAX_MODEL_BYTES:
                raise SnapshotError("forecast_model_pipeline_size_mismatch")
        if (
            not receipt.path.startswith("models/")
            or receipt.size_bytes > MAX_MODEL_BYTES
            or file_hash(root, receipt.path) != (receipt.size_bytes, receipt.sha256)
        ):
            raise SnapshotError("forecast_model_pipeline_receipt_mismatch")
        pipeline = ModelPipeline.model_validate_json(
            read_bytes(root, receipt.path, MAX_MODEL_BYTES)
        )
        if (
            pipeline.model_id != manifest.descriptor.models[name]
            or pipeline.descriptor.feature_set_id != manifest.descriptor.feature_set_id
            or pipeline.descriptor.split_id != manifest.descriptor.split_id
            or pipeline.descriptor.code != manifest.descriptor.code
            or pipeline.descriptor.policy != manifest.descriptor.policy
            or name
            != pipeline.descriptor.preprocessing.fold.name + ":" + pipeline.descriptor.family
        ):
            raise SnapshotError("forecast_model_pipeline_parent_binding_mismatch")
        loaded_pipelines[name] = pipeline
        expected.add(receipt.path)
    ref = manifest.predictions.files
    if (
        len(ref) != 1
        or ref[0].path != "predictions.jsonl"
        or ref[0].row_count != manifest.predictions.row_count
        or ref[0].size_bytes > MAX_BYTES
        or manifest.predictions.row_count > MAX_PREDICTIONS
    ):
        raise SnapshotError("forecast_model_prediction_receipt_invalid")
    with regular_file(root, ref[0].path) as stream:
        stream.seek(0, 2)
        if stream.tell() != ref[0].size_bytes:
            raise SnapshotError("forecast_model_prediction_size_mismatch")
    if file_hash(root, ref[0].path) != (ref[0].size_bytes, ref[0].sha256):
        raise SnapshotError("forecast_model_prediction_checksum_mismatch")
    previous: tuple[bytes, str] | None = None
    digest = hashlib.sha256()
    count = 0
    with regular_file(root, ref[0].path) as stream:
        while line := stream.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE or not line.endswith(b"\n"):
                raise SnapshotError("forecast_model_prediction_line_limit")
            payload = decode_json(line)
            row = (
                ModelPrediction.model_validate_json(line)
                if payload["model"] in LEARNED
                else BaselinePrediction.model_validate_json(line)
            )
            ordered = key(row), row.model
            if (
                previous is not None
                and ordered <= previous
                or line != canonical_bytes(row.model_dump(mode="json")) + b"\n"
            ):
                raise SnapshotError("forecast_model_predictions_not_canonical_unique_ordered")
            if isinstance(row, ModelPrediction):
                prediction_pipeline = loaded_pipelines.get(row.fold + ":" + row.model)
                if prediction_pipeline is None or (
                    prediction_pipeline.model_id != row.model_id
                    or prediction_pipeline.descriptor.preprocessing.fold.training_cutoff
                    != row.training_knowledge_cutoff
                ):
                    raise SnapshotError("forecast_model_prediction_model_binding_mismatch")
            previous = ordered
            count += 1
            digest.update(line)
    if (
        count != manifest.predictions.row_count
        or digest.hexdigest() != manifest.predictions.content_sha256
    ):
        raise SnapshotError("forecast_model_prediction_content_mismatch")
    if read_bytes(root, "model_card.json") != canonical_bytes(model_card(manifest)) + b"\n":
        raise SnapshotError("forecast_model_card_binding_mismatch")
    inventory(root, expected)
    return manifest


def build_comparison(
    feature_dir: Path, split_dir: Path, output_root: Path, policy: ModelPolicy | None = None
) -> Path:
    policy = (
        ModelPolicy()
        if policy is None
        else ModelPolicy.model_validate_json(policy.model_dump_json())
    )
    output_root = output_root.absolute()
    if any(output_root.is_relative_to(source.absolute()) for source in (feature_dir, split_dir)):
        raise SnapshotError("forecast_model_output_inside_input")
    output_root.mkdir(parents=True, exist_ok=True)
    checked_directory(output_root)
    with tempfile.TemporaryDirectory(prefix=".models-", dir=output_root) as temporary:
        root = Path(temporary)
        with tempfile.TemporaryDirectory(prefix="forecast-models-db-") as database:
            with sqlite3.connect(Path(database) / "index.sqlite") as db:
                manifest = _report(root, db, feature_dir, split_dir, policy)
        load_comparison(root)
        fsync_tree(root)
        destination = output_root / manifest.comparison_id
        try:
            publish_noreplace(root, destination)
        except FileExistsError:
            existing = load_comparison(destination)
            if (
                existing.descriptor != manifest.descriptor
                or existing.predictions != manifest.predictions
            ):
                raise SnapshotError("forecast_model_comparison_publication_conflict") from None
        return destination


def verify_comparison(root: Path, feature_dir: Path, split_dir: Path) -> ModelRunManifest:
    manifest = load_comparison(root)
    if manifest.descriptor.code != model_code():
        raise SnapshotError("forecast_model_verification_code_pin_mismatch")
    with tempfile.TemporaryDirectory(prefix="forecast-model-verify-") as temporary:
        workspace = Path(temporary).resolve()
        with sqlite3.connect(workspace / "index.sqlite") as db:
            expected = _report(workspace, db, feature_dir, split_dir, manifest.descriptor.policy)
        if (
            expected.descriptor != manifest.descriptor
            or expected.predictions != manifest.predictions
        ):
            raise SnapshotError("forecast_model_retraining_replay_mismatch")
    return manifest
