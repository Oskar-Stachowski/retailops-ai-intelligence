"""One bounded evaluator for immutable common keys; selection precedes holdout scoring."""

from __future__ import annotations

import hashlib
import math
import sqlite3
import tempfile
from collections import Counter
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.baselines import predict
from retailops_ai.forecasting.evaluation_contract import (
    BaselineName,
    BaselinePolicy,
    BaselinePrediction,
    BaselineSelection,
    EvaluationCode,
    EvaluationDescriptor,
    EvaluationManifest,
    MetricResult,
)
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifest_contract import (
    FileReceipt,
    LabelPoint,
    Membership,
    TableReceipt,
)
from retailops_ai.forecasting.manifest_io import MAX_BYTES, code_pin, dump, iter_table, key
from retailops_ai.forecasting.manifests import feature_key, input_models
from retailops_ai.forecasting.splits import history_index, verify_split
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

MAX_PREDICTIONS = 3000000
MAX_LINE = 64 * 1024


def evaluation_code() -> EvaluationCode:
    parent = code_pin()
    hashes = {
        **parent.code_files,
        **{
            "forecasting/" + name: hashlib.sha256(
                files("retailops_ai.forecasting").joinpath(name).read_bytes()
            ).hexdigest()
            for name in (
                "evaluation_contract.py",
                "baselines.py",
                "evaluation.py",
                "features_contract.py",
            )
        },
    }
    return EvaluationCode(
        code_files=hashes,
        code_sha256=canonical_sha256(hashes),
        dependency_lock_sha256=parent.dependency_lock_sha256,
        python_version=parent.python_version,
        pyarrow_version=parent.pyarrow_version,
    )


class MetricAccumulator:
    """Zeros enter error sums. Any absent prediction invalidates the whole common-key metric."""

    def __init__(self) -> None:
        self.eligible = self.predicted = 0
        self.errors = self.actuals = 0.0

    def add(self, actual: int, predicted: float | None) -> None:
        if (
            type(actual) is not int
            or actual < 0
            or (
                predicted is not None
                and (type(predicted) is not float or not math.isfinite(predicted) or predicted < 0)
            )
        ):
            raise SnapshotError("baseline_metric_invalid_quantity")
        self.eligible += 1
        if predicted is not None:
            self.predicted += 1
            self.errors += abs(actual - predicted)
            self.actuals += abs(actual)

    def result(self) -> MetricResult:
        complete = self.eligible > 0 and self.eligible == self.predicted
        status = "passed" if complete else "incomplete" if self.eligible else "not_evaluable"
        return MetricResult.model_validate(
            {
                "eligible_rows": self.eligible,
                "predicted_rows": self.predicted,
                "status": status,
                "mae": self.errors / self.eligible if complete else None,
                "wape": self.errors / self.actuals if complete and self.actuals else None,
                "absolute_error_sum": self.errors if complete else None,
                "absolute_actual_sum": self.actuals if complete else None,
                "wape_status": "passed"
                if complete and self.actuals
                else "zero_denominator"
                if complete
                else "incomplete"
                if self.eligible
                else "no_rows",
            }
        )


def select_validation(
    policy: BaselinePolicy,
    metrics: dict[BaselineName, MetricResult],
    keys_sha256: str,
    *,
    fold: str,
    feature_set_id: str,
    split_id: str,
) -> BaselineSelection:
    if set(metrics) != set(policy.candidates):
        raise SnapshotError("baseline_selection_candidate_set_mismatch")
    if len({metric.eligible_rows for metric in metrics.values()}) != 1:
        raise SnapshotError("baseline_selection_common_count_mismatch")
    complete = all(metrics[name].status == "passed" for name in policy.candidates)
    names: tuple[BaselineName, ...] = policy.candidates

    def rank(name: BaselineName) -> tuple[float, int]:
        mae = metrics[name].mae
        if mae is None:
            raise SnapshotError("baseline_selection_metric_missing")
        return mae, names.index(name)

    chosen: BaselineName | None = sorted(names, key=rank)[0] if complete else None
    body = {
        "feature_set_id": feature_set_id,
        "split_id": split_id,
        "fold": fold,
        "policy": policy.model_dump(mode="json"),
        "validation_grain_sha256": keys_sha256,
        "validation_metrics": {
            name: metric.model_dump(mode="json") for name, metric in metrics.items()
        },
        "model": chosen,
    }
    return BaselineSelection(
        fold=fold,
        status="selected" if chosen else "not_ready",
        model=chosen,
        validation_metrics=metrics,
        validation_grain_sha256=keys_sha256,
        selection_sha256=canonical_sha256(body),
    )


def _database(
    db: sqlite3.Connection, feature_dir: Path, split_dir: Path, policy: BaselinePolicy
) -> tuple[Any, Counter[str]]:
    split = verify_split(split_dir, feature_dir)
    if split.descriptor.qualification_status != "passed":
        raise SnapshotError("baseline_split_not_ready_no_partial_run")
    # Cap before opening feature/prediction buffers. Three models cover every membership.
    if split.tables["memberships"].row_count * len(policy.candidates) > MAX_PREDICTIONS:
        raise SnapshotError("baseline_evaluation_resource_limit")
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    db.execute(f"PRAGMA max_page_count={MAX_BYTES // page_size}")
    db.execute("CREATE TABLE members (key BLOB PRIMARY KEY, feature_key BLOB, body BLOB)")
    db.execute("CREATE INDEX member_features ON members(feature_key)")
    db.execute("CREATE TABLE labels (key BLOB PRIMARY KEY, body BLOB)")
    db.execute(
        "CREATE TABLE predictions (key BLOB, model TEXT, fold TEXT, role TEXT, eligible INTEGER, units REAL, body BLOB, PRIMARY KEY(key,model))"
    )
    budget: Counter[str] = Counter()
    coverage: Counter[str] = Counter()
    for name in ("memberships", "labels"):
        for record in iter_table(split_dir, name, split.tables[name], budget):
            body = canonical_bytes(record.model_dump(mode="json"))
            if isinstance(record, Membership):
                db.execute(
                    "INSERT INTO members VALUES (?,?,?)", (key(record), feature_key(record), body)
                )
                prefix = record.fold + ":" + record.role + ":"
                coverage[prefix + "total"] += 1
                coverage[prefix + "eligible"] += int(record.eligible)
                for reason in record.reasons:
                    coverage[prefix + "excluded:" + reason] += 1
            else:
                db.execute("INSERT INTO labels VALUES (?,?)", (key(record), body))
    get_history = history_index(db, feature_dir)
    seen = 0
    for row in input_models(feature_dir, "features"):
        if not isinstance(row, InputRow):
            raise SnapshotError("baseline_feature_schema_mismatch")
        history = get_history(row.history_context_sha256)
        for member_key, body in db.execute(
            "SELECT key,body FROM members WHERE feature_key=? ORDER BY key", (feature_key(row),)
        ):
            member = Membership.model_validate_json(body)
            seen += 1
            for model in policy.candidates:
                estimate = predict(model, row, history, policy) if member.eligible else None
                prediction = BaselinePrediction(
                    **row.model_dump(include=set(ForecastKey.model_fields)),
                    fold=member.fold,
                    role=member.role,
                    model=model,
                    eligible=member.eligible,
                    exclusion_reasons=member.reasons,
                    estimate=estimate,
                )
                units = estimate.predicted_units if estimate else None
                body = canonical_bytes(prediction.model_dump(mode="json"))
                if len(body) + 1 > MAX_LINE:
                    raise SnapshotError("baseline_prediction_line_limit")
                db.execute(
                    "INSERT INTO predictions VALUES (?,?,?,?,?,?,?)",
                    (member_key, model, member.fold, member.role, member.eligible, units, body),
                )
                if member.eligible:
                    coverage[
                        member.fold
                        + ":"
                        + member.role
                        + ":"
                        + model
                        + (":predicted" if units is not None else ":missing_prediction")
                    ] += 1
    if seen != split.tables["memberships"].row_count:
        raise SnapshotError("baseline_common_key_coverage_mismatch")
    return split, coverage


def _metrics(db: sqlite3.Connection, fold: str, role: str, model: BaselineName) -> MetricResult:
    metric = MetricAccumulator()
    for units, body in db.execute(
        "SELECT p.units,l.body FROM predictions p LEFT JOIN labels l ON l.key=p.key WHERE p.fold=? AND p.role=? AND p.model=? AND p.eligible=1 ORDER BY p.key",
        (fold, role, model),
    ):
        if body is None:
            raise SnapshotError("baseline_scoring_label_key_missing")
        label = LabelPoint.model_validate_json(body)
        if label.status != "eligible" or label.observed_sales_units is None:
            raise SnapshotError("baseline_scoring_label_not_eligible")
        metric.add(label.observed_sales_units, units)
    return metric.result()


def _deferred_holdout(db: sqlite3.Connection, fold: str, model: BaselineName) -> MetricResult:
    eligible, predicted = db.execute(
        "SELECT COUNT(*),COALESCE(SUM(units IS NOT NULL),0) FROM predictions WHERE fold=? AND role='development_holdout' AND model=? AND eligible=1",
        (fold, model),
    ).fetchone()
    return MetricResult(
        eligible_rows=eligible,
        predicted_rows=predicted,
        status="selection_not_ready",
        mae=None,
        wape=None,
        absolute_error_sum=None,
        absolute_actual_sum=None,
        wape_status="selection_not_ready",
    )


def _report(
    root: Path, db: sqlite3.Connection, feature_dir: Path, split_dir: Path, policy: BaselinePolicy
) -> EvaluationManifest:
    split, coverage = _database(db, feature_dir, split_dir, policy)
    metrics: dict[str, MetricResult] = {}
    selections = []
    for fold in split.descriptor.resolved_policy.folds:
        validation = {
            model: _metrics(db, fold.name, "validation", model) for model in policy.candidates
        }
        digest = hashlib.sha256()
        for (row_key,) in db.execute(
            "SELECT DISTINCT key FROM predictions WHERE fold=? AND role='validation' AND eligible=1 ORDER BY key",
            (fold.name,),
        ):
            digest.update(row_key + b"\n")
        selection = select_validation(
            policy,
            validation,
            digest.hexdigest(),
            fold=fold.name,
            feature_set_id=split.descriptor.feature_set_id,
            split_id=split.split_id,
        )
        selections.append(selection)
        # No holdout outcomes were read by predict or select_validation. The choice is now pinned.
        for role in ("train", "validation", "development_holdout"):
            for model in policy.candidates:
                metrics[fold.name + ":" + role + ":" + model] = (
                    validation[model]
                    if role == "validation"
                    else _deferred_holdout(db, fold.name, model)
                    if role == "development_holdout" and selection.model is None
                    else _metrics(db, fold.name, role, model)
                )
    digest = hashlib.sha256()
    count = size = 0
    path = root / "predictions.jsonl"
    with path.open("xb") as stream:
        for (body,) in db.execute("SELECT body FROM predictions ORDER BY key,model"):
            line = body + b"\n"
            count += 1
            size += len(line)
            if count > MAX_PREDICTIONS or size > MAX_BYTES:
                raise SnapshotError("baseline_evaluation_resource_limit")
            stream.write(line)
            digest.update(line)
    db_path = Path(db.execute("PRAGMA database_list").fetchone()[2])
    if db_path.stat().st_size > MAX_BYTES:
        raise SnapshotError("baseline_evaluation_database_limit")
    file_size, physical_sha = file_hash(root, path.name)
    receipt = TableReceipt(
        content_sha256=digest.hexdigest(),
        row_count=count,
        files=(
            FileReceipt(path=path.name, size_bytes=file_size, sha256=physical_sha, row_count=count),
        ),
    )
    descriptor = EvaluationDescriptor(
        feature_set_id=split.descriptor.feature_set_id,
        split_id=split.split_id,
        label_dataset_id=split.descriptor.label_dataset_id,
        requested_policy=policy,
        resolved_policy=policy,
        code=evaluation_code(),
        predictions_content_sha256=receipt.content_sha256,
        prediction_rows=count,
        coverage_counts=dict(coverage),
        selections=tuple(selections),
        metrics=metrics,
        status="passed"
        if all(selection.status == "selected" for selection in selections)
        and all(metric.status == "passed" for metric in metrics.values())
        else "not_ready",
    )
    manifest = EvaluationManifest(
        evaluation_id="forecast-evaluation-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
        predictions=receipt,
        generated_at=datetime.now(UTC),
    )
    dump(root / "evaluation_manifest.json", manifest)
    return manifest


def load_evaluation(root: Path) -> EvaluationManifest:
    """Validate receipt, exact inventory and ordered common-key prediction records; no quality claim."""
    checked_directory(root)
    raw = read_bytes(root, "evaluation_manifest.json")
    decode_json(raw)
    manifest = EvaluationManifest.model_validate_json(raw)
    if len(manifest.predictions.files) != 1:
        raise SnapshotError("baseline_prediction_file_count_mismatch")
    ref = manifest.predictions.files[0]
    if (
        ref.path != "predictions.jsonl"
        or ref.row_count != manifest.predictions.row_count
        or ref.size_bytes > MAX_BYTES
        or ref.row_count > MAX_PREDICTIONS
    ):
        raise SnapshotError("baseline_prediction_receipt_invalid")
    with regular_file(root, ref.path) as stream:
        stream.seek(0, 2)
        if stream.tell() != ref.size_bytes:
            raise SnapshotError("baseline_prediction_size_mismatch")
    if file_hash(root, ref.path) != (ref.size_bytes, ref.sha256):
        raise SnapshotError("baseline_prediction_checksum_mismatch")
    digest = hashlib.sha256()
    count = 0
    previous: tuple[bytes, str] | None = None
    with regular_file(root, ref.path) as stream:
        while line := stream.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE or not line.endswith(b"\n"):
                raise SnapshotError("baseline_prediction_line_limit")
            decode_json(line)
            row = BaselinePrediction.model_validate_json(line)
            ordered = (key(row), row.model)
            if (
                previous is not None
                and ordered <= previous
                or canonical_bytes(row.model_dump(mode="json")) + b"\n" != line
            ):
                raise SnapshotError("baseline_predictions_not_canonical_unique_ordered")
            previous = ordered
            digest.update(line)
            count += 1
    if count != ref.row_count or digest.hexdigest() != manifest.predictions.content_sha256:
        raise SnapshotError("baseline_prediction_content_mismatch")
    inventory(root, {"evaluation_manifest.json", ref.path})
    return manifest


def build_evaluation(
    feature_dir: Path, split_dir: Path, output_root: Path, policy: BaselinePolicy | None = None
) -> Path:
    policy = (
        BaselinePolicy()
        if policy is None
        else BaselinePolicy.model_validate_json(policy.model_dump_json())
    )
    output_root = output_root.absolute()
    if any(output_root.is_relative_to(source.absolute()) for source in (feature_dir, split_dir)):
        raise SnapshotError("baseline_output_inside_input")
    output_root.mkdir(parents=True, exist_ok=True)
    checked_directory(output_root)
    with tempfile.TemporaryDirectory(prefix=".baseline-", dir=output_root) as temporary:
        root = Path(temporary)
        with tempfile.TemporaryDirectory(prefix="baseline-db-") as database:
            with sqlite3.connect(Path(database) / "index.sqlite") as db:
                manifest = _report(root, db, feature_dir, split_dir, policy)
        load_evaluation(root)
        fsync_tree(root)
        destination = output_root / manifest.evaluation_id
        try:
            publish_noreplace(root, destination)
        except FileExistsError:
            existing = load_evaluation(destination)
            if (
                existing.descriptor != manifest.descriptor
                or existing.predictions != manifest.predictions
            ):
                raise SnapshotError("baseline_evaluation_publication_conflict") from None
        return destination


def verify_evaluation(root: Path, feature_dir: Path, split_dir: Path) -> EvaluationManifest:
    """Recompute from verified parents: rehashed forged quantities/metrics cannot pass."""
    manifest = load_evaluation(root)
    if manifest.descriptor.code != evaluation_code():
        raise SnapshotError("baseline_verification_code_pin_mismatch")
    with tempfile.TemporaryDirectory(prefix="baseline-verify-") as temporary:
        workspace = Path(temporary).resolve()
        with sqlite3.connect(workspace / "index.sqlite") as db:
            expected = _report(
                workspace, db, feature_dir, split_dir, manifest.descriptor.resolved_policy
            )
        if (
            expected.descriptor != manifest.descriptor
            or expected.predictions != manifest.predictions
        ):
            raise SnapshotError("baseline_evaluation_recomputation_mismatch")
    return manifest
