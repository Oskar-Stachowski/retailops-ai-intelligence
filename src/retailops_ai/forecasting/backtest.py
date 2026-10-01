"""Chronological orchestration around the existing split, fitting and scoring engine."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import tempfile
from collections import Counter
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.backtest_contract import (
    EVALUATION_ROLES,
    METHODS,
    BacktestCode,
    BacktestDescriptor,
    BacktestManifest,
    BacktestPolicy,
    FoldAudit,
    plan_backtest,
)
from retailops_ai.forecasting.calendar import load_calendar
from retailops_ai.forecasting.evaluation import MAX_LINE
from retailops_ai.forecasting.evaluation_contract import BaselinePrediction, MetricResult
from retailops_ai.forecasting.manifest_contract import LabelPoint, Membership, SplitManifest
from retailops_ai.forecasting.manifest_io import MAX_BYTES, dump, iter_table, key
from retailops_ai.forecasting.manifests import load_feature_set
from retailops_ai.forecasting.model_contract import (
    LEARNED_NAMES,
    ModelPipeline,
    ModelPrediction,
    ModelRunManifest,
)
from retailops_ai.forecasting.models import build_comparison, load_comparison, model_code
from retailops_ai.forecasting.splits import build_split, load_split
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    inventory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace


def backtest_code() -> BacktestCode:
    hashes = {
        "forecasting/" + name: hashlib.sha256(
            files("retailops_ai.forecasting").joinpath(name).read_bytes()
        ).hexdigest()
        for name in ("backtest_contract.py", "backtest.py")
    }
    return BacktestCode(code_files=hashes, code_sha256=canonical_sha256(hashes), model=model_code())


def pool_metrics(metrics: list[MetricResult]) -> MetricResult:
    """Pool sums and counts, including zeros; incomplete folds cannot disappear."""
    eligible = sum(metric.eligible_rows for metric in metrics)
    predicted = sum(metric.predicted_rows for metric in metrics)
    status: Literal["passed", "incomplete", "not_evaluable", "selection_not_ready"] = (
        "selection_not_ready"
        if any(metric.status == "selection_not_ready" for metric in metrics)
        or eligible > 0
        and any(metric.status == "not_evaluable" for metric in metrics)
        else "not_evaluable"
        if not eligible
        else "incomplete"
        if any(metric.status != "passed" for metric in metrics)
        else "passed"
    )
    error = (
        math.fsum(
            metric.absolute_error_sum for metric in metrics if metric.absolute_error_sum is not None
        )
        if status == "passed"
        else None
    )
    actual = (
        math.fsum(
            metric.absolute_actual_sum
            for metric in metrics
            if metric.absolute_actual_sum is not None
        )
        if status == "passed"
        else None
    )
    return MetricResult(
        eligible_rows=eligible,
        predicted_rows=predicted,
        status=status,
        mae=error / eligible if error is not None else None,
        wape=error / actual if error is not None and actual else None,
        absolute_error_sum=error,
        absolute_actual_sum=actual,
        wape_status="passed"
        if actual
        else "zero_denominator"
        if status == "passed"
        else "no_rows"
        if status == "not_evaluable"
        else "selection_not_ready"
        if status == "selection_not_ready"
        else "incomplete",
    )


def pooled_report(comparison: ModelRunManifest) -> dict[str, MetricResult]:
    result = {}
    for role in EVALUATION_ROLES:
        for method in METHODS:
            metrics = []
            for selection in comparison.descriptor.selections:
                selected = selection.selected if method == "validation_selected" else method
                if selected is None:
                    count = comparison.descriptor.metrics[
                        selection.fold + ":" + role + ":last_observed"
                    ].eligible_rows
                    metrics.append(
                        MetricResult(
                            eligible_rows=count,
                            predicted_rows=0,
                            status="selection_not_ready",
                            mae=None,
                            wape=None,
                            absolute_error_sum=None,
                            absolute_actual_sum=None,
                            wape_status="selection_not_ready",
                        )
                    )
                else:
                    metrics.append(
                        comparison.descriptor.metrics[selection.fold + ":" + role + ":" + selected]
                    )
            result[role + ":" + method] = pool_metrics(metrics)
    return result


def audit_folds(
    root: Path, split: SplitManifest, comparison: ModelRunManifest
) -> tuple[FoldAudit, ...]:
    """Verify every method's keys/reasons and all fitted label cutoffs against the split."""
    with tempfile.TemporaryDirectory(prefix="backtest-audit-") as temporary:
        with sqlite3.connect(Path(temporary) / "keys.sqlite") as db:
            size = db.execute("PRAGMA page_size").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={MAX_BYTES // size}")
            db.execute("PRAGMA cache_size=-4096")
            db.execute("PRAGMA temp_store=FILE")
            db.execute("CREATE TABLE members (key BLOB PRIMARY KEY,fold TEXT,body BLOB)")
            db.execute("CREATE INDEX member_folds ON members(fold)")
            db.execute("CREATE TABLE labels (key BLOB PRIMARY KEY,body BLOB)")
            budget: Counter[str] = Counter()
            for kind in ("memberships", "labels"):
                for record in iter_table(root / "split", kind, split.tables[kind], budget):
                    body = canonical_bytes(record.model_dump(mode="json"))
                    if kind == "memberships":
                        db.execute(
                            "INSERT INTO members VALUES (?,?,?)", (key(record), record.fold, body)
                        )
                    else:
                        db.execute("INSERT INTO labels VALUES (?,?)", (key(record), body))
                digest = hashlib.sha256()
                count = 0
                statement = (
                    "SELECT body FROM members ORDER BY key"
                    if kind == "memberships"
                    else "SELECT body FROM labels ORDER BY key"
                )
                for (body,) in db.execute(statement):
                    digest.update(body + b"\n")
                    count += 1
                if (
                    count != split.tables[kind].row_count
                    or digest.hexdigest() != split.tables[kind].content_sha256
                ):
                    raise SnapshotError("backtest_split_logical_content_mismatch")
            expected_methods = set(METHODS) - {"validation_selected"}
            previous = None
            seen: set[str] = set()
            groups = 0
            with regular_file(root / "comparison", "predictions.jsonl") as stream:
                while line := stream.readline(MAX_LINE + 1):
                    if len(line) > MAX_LINE or not line.endswith(b"\n"):
                        raise SnapshotError("backtest_prediction_line_limit")
                    payload = json.loads(line)
                    prediction = (
                        ModelPrediction.model_validate_json(line)
                        if payload["model"] in LEARNED_NAMES
                        else BaselinePrediction.model_validate_json(line)
                    )
                    grain = key(prediction)
                    if grain != previous:
                        if previous is not None and grain <= previous:
                            raise SnapshotError("backtest_prediction_keys_not_unique_ordered")
                        if previous is not None and seen != expected_methods:
                            raise SnapshotError("backtest_common_prediction_methods_missing")
                        seen = set()
                        previous = grain
                        groups += 1
                        raw = db.execute(
                            "SELECT body FROM members WHERE key=?", (grain,)
                        ).fetchone()
                        if raw is None:
                            raise SnapshotError("backtest_prediction_outside_membership")
                        member = Membership.model_validate_json(raw[0])
                    if prediction.model in seen or (
                        prediction.eligible != member.eligible
                        or prediction.exclusion_reasons != member.reasons
                    ):
                        raise SnapshotError("backtest_model_specific_coverage_mismatch")
                    seen.add(prediction.model)
            if seen != expected_methods or groups != split.tables["memberships"].row_count:
                raise SnapshotError("backtest_common_prediction_key_count_mismatch")
            audits = []
            counts: Counter[str] = Counter()
            for fold in split.descriptor.resolved_policy.folds:
                grain_digest = hashlib.sha256()
                label_digest = hashlib.sha256()
                latest = None
                train_rows = total = 0
                for member_key, member_body, label_body in db.execute(
                    "SELECT m.key,m.body,l.body FROM members m LEFT JOIN labels l ON m.key=l.key WHERE m.fold=? ORDER BY m.key",
                    (fold.name,),
                ):
                    member = Membership.model_validate_json(member_body)
                    if member.fold != fold.name:
                        continue
                    if member.role != fold.role(member.forecast_origin.date()):
                        raise SnapshotError("backtest_member_role_mismatch")
                    total += 1
                    grain_digest.update(member_key + b"\n")
                    counts[member.role] += int(member.eligible)
                    if label_body is not None:
                        label = LabelPoint.model_validate_json(label_body)
                        if label.knowledge_cutoff != fold.label_cutoff(member.role):
                            raise SnapshotError("backtest_label_knowledge_cutoff_mismatch")
                    if member.role != "train" or not member.eligible:
                        continue
                    if label_body is None:
                        raise SnapshotError("backtest_missing_training_label")
                    label = LabelPoint.model_validate_json(label_body)
                    if (
                        label.status != "eligible"
                        or label.label_available_at is None
                        or label.label_available_at > fold.training_cutoff
                    ):
                        raise SnapshotError("backtest_future_training_label")
                    latest = (
                        max(latest, label.label_available_at)
                        if latest is not None
                        else label.label_available_at
                    )
                    train_rows += 1
                    label_digest.update(canonical_bytes(label.model_dump(mode="json")) + b"\n")
                if latest is None:
                    raise SnapshotError("backtest_no_eligible_training_labels")
                model_ids = {}
                for family in LEARNED_NAMES:
                    receipt = comparison.pipelines[fold.name + ":" + family]
                    pipeline = ModelPipeline.model_validate_json(
                        read_bytes(root / "comparison", receipt.path, 128 * 1024**2)
                    )
                    if (
                        pipeline.descriptor.preprocessing.fold != fold
                        or pipeline.descriptor.preprocessing.train_rows != train_rows
                        or pipeline.descriptor.train_labels_content_sha256
                        != label_digest.hexdigest()
                    ):
                        raise SnapshotError("backtest_fold_training_binding_mismatch")
                    model_ids[family] = pipeline.model_id
                audits.append(
                    FoldAudit(
                        plan=fold,
                        eligible_train_rows=train_rows,
                        latest_train_label_available_at=latest,
                        train_labels_content_sha256=label_digest.hexdigest(),
                        model_ids=model_ids,
                        common_prediction_keys=total,
                        common_grain_sha256=grain_digest.hexdigest(),
                        eligible_counts={
                            role: counts[role]
                            for role in ("train", "validation", "development_holdout", "purged")
                        },
                    )
                )
                counts.clear()
    return tuple(audits)


def backtest_card(manifest: BacktestManifest) -> dict[str, Any]:
    descriptor = manifest.descriptor
    return {
        "backtest_id": manifest.backtest_id,
        "feature_set_id": descriptor.feature_set_id,
        "split_id": descriptor.split_id,
        "comparison_id": descriptor.comparison_id,
        "target_type": descriptor.target_type,
        "mode": descriptor.policy.mode,
        "folds": len(descriptor.folds),
        "forecast_model_status": "not_ready_pending_AI_04_7_and_04_8",
        "portfolio_final_test": descriptor.policy.portfolio_final_test,
        "development_holdout_use": descriptor.policy.development_holdout_use,
        "legacy_retailops_serving": descriptor.policy.model.legacy_retailops_serving,
        "limitations": [
            "development evidence on synthetic data, not commercial model quality",
            "observed sales can be constrained by inventory; inventory/truth features excluded",
            "same-role forecast origins do not repeat; target dates can repeat for distinct origins",
            "training scores are in-sample diagnostics, pooled reports cover evaluation roles only",
            "each fold selects on validation; pooled results do not select or promote a champion",
        ],
    }


def _write_reports(root: Path, manifest: BacktestManifest) -> None:
    for name, value in (
        ("config.json", manifest.descriptor.policy.model_dump(mode="json")),
        ("model_card.json", backtest_card(manifest)),
        (
            "leakage_report.json",
            [audit.model_dump(mode="json") for audit in manifest.descriptor.folds],
        ),
        (
            "metrics.json",
            {
                name: metric.model_dump(mode="json")
                for name, metric in manifest.descriptor.pooled_metrics.items()
            },
        ),
    ):
        (root / name).write_bytes(canonical_bytes(value) + b"\n")


def _assemble(
    root: Path, feature_dir: Path, curated_dir: Path, policy: BacktestPolicy
) -> BacktestManifest:
    code = backtest_code()
    feature = load_feature_set(feature_dir)
    calendar = load_calendar(feature_dir / "inputs/calendar_manifest.json")
    resolved = plan_backtest(calendar.descriptor.origin_window, policy)
    split_directory = build_split(feature_dir, curated_dir, root / "splits", resolved)
    split_directory.rename(root / "split")
    (root / "splits").rmdir()
    split = load_split(root / "split")
    comparison_directory = build_comparison(
        feature_dir, root / "split", root / "comparisons", policy.model
    )
    comparison_directory.rename(root / "comparison")
    (root / "comparisons").rmdir()
    comparison = load_comparison(root / "comparison")
    metrics = pooled_report(comparison)
    descriptor = BacktestDescriptor(
        feature_set_id=feature.feature_set_id,
        parent=feature.descriptor.parent,
        origin_window=calendar.descriptor.origin_window,
        policy=policy,
        resolved_split=resolved,
        split_id=split.split_id,
        label_dataset_id=split.descriptor.label_dataset_id,
        comparison_id=comparison.comparison_id,
        comparison_descriptor_sha256=canonical_sha256(
            comparison.descriptor.model_dump(mode="json")
        ),
        comparison_status=comparison.descriptor.status,
        code=code,
        folds=audit_folds(root, split, comparison),
        pooled_metrics=metrics,
        status="passed"
        if comparison.descriptor.status == "passed"
        and all(metric.status == "passed" for metric in metrics.values())
        else "not_ready",
    )
    manifest = BacktestManifest(
        backtest_id="forecast-backtest-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
        generated_at=datetime.now(UTC),
    )
    _write_reports(root, manifest)
    if code != backtest_code():
        raise SnapshotError("backtest_implementation_changed_during_run")
    dump(root / "backtest_manifest.json", manifest)
    return manifest


def load_backtest(root: Path) -> BacktestManifest:
    checked_directory(root)
    raw = read_bytes(root, "backtest_manifest.json")
    decode_json(raw)
    manifest = BacktestManifest.model_validate_json(raw)
    descriptor = manifest.descriptor
    split = load_split(root / "split")
    comparison = load_comparison(root / "comparison")
    if (
        split.split_id != descriptor.split_id
        or split.descriptor.feature_set_id != descriptor.feature_set_id
        or split.descriptor.parent != descriptor.parent
        or split.descriptor.resolved_policy != descriptor.resolved_split
        or split.descriptor.label_dataset_id != descriptor.label_dataset_id
        or comparison.comparison_id != descriptor.comparison_id
        or comparison.descriptor.feature_set_id != descriptor.feature_set_id
        or comparison.descriptor.parent != descriptor.parent
        or comparison.descriptor.split_id != descriptor.split_id
        or comparison.descriptor.label_dataset_id != descriptor.label_dataset_id
        or comparison.descriptor.policy != descriptor.policy.model
        or comparison.descriptor.code != descriptor.code.model
        or comparison.descriptor.status != descriptor.comparison_status
        or canonical_sha256(comparison.descriptor.model_dump(mode="json"))
        != descriptor.comparison_descriptor_sha256
    ):
        raise SnapshotError("backtest_child_binding_mismatch")
    if (
        pooled_report(comparison) != descriptor.pooled_metrics
        or audit_folds(root, split, comparison) != descriptor.folds
    ):
        raise SnapshotError("backtest_report_replay_mismatch")
    for name, value in (
        ("config.json", descriptor.policy.model_dump(mode="json")),
        ("model_card.json", backtest_card(manifest)),
        ("leakage_report.json", [audit.model_dump(mode="json") for audit in descriptor.folds]),
        (
            "metrics.json",
            {
                name: metric.model_dump(mode="json")
                for name, metric in descriptor.pooled_metrics.items()
            },
        ),
    ):
        if read_bytes(root, name) != canonical_bytes(value) + b"\n":
            raise SnapshotError("backtest_control_report_binding_mismatch")
    names = {
        "backtest_manifest.json",
        "config.json",
        "model_card.json",
        "leakage_report.json",
        "metrics.json",
        "split/split_manifest.json",
        "comparison/comparison_manifest.json",
        "comparison/predictions.jsonl",
        "comparison/model_card.json",
    }
    names.update(
        "split/" + receipt.path for table in split.tables.values() for receipt in table.files
    )
    names.update("comparison/" + receipt.path for receipt in comparison.pipelines.values())
    inventory(root, names)
    return manifest


def build_backtest(
    feature_dir: Path, curated_dir: Path, output_root: Path, policy: BacktestPolicy | None = None
) -> Path:
    policy = (
        BacktestPolicy()
        if policy is None
        else BacktestPolicy.model_validate_json(policy.model_dump_json())
    )
    output_root = output_root.absolute()
    if any(output_root.is_relative_to(path.absolute()) for path in (feature_dir, curated_dir)):
        raise SnapshotError("backtest_output_inside_input")
    output_root.mkdir(parents=True, exist_ok=True)
    checked_directory(output_root)
    with tempfile.TemporaryDirectory(prefix=".backtest-", dir=output_root) as temporary:
        root = Path(temporary)
        manifest = _assemble(root, feature_dir, curated_dir, policy)
        load_backtest(root)
        fsync_tree(root)
        destination = output_root / manifest.backtest_id
        try:
            publish_noreplace(root, destination)
        except FileExistsError:
            if load_backtest(destination).descriptor != manifest.descriptor:
                raise SnapshotError("backtest_immutable_publication_conflict") from None
        return destination


def verify_backtest(root: Path, feature_dir: Path, curated_dir: Path) -> BacktestManifest:
    manifest = load_backtest(root)
    if manifest.descriptor.code != backtest_code():
        raise SnapshotError("backtest_verification_code_pin_mismatch")
    with tempfile.TemporaryDirectory(prefix="backtest-verify-") as temporary:
        expected = _assemble(
            Path(temporary).resolve(), feature_dir, curated_dir, manifest.descriptor.policy
        )
        if expected.descriptor != manifest.descriptor:
            raise SnapshotError("backtest_source_rebuild_and_retraining_mismatch")
    return manifest
