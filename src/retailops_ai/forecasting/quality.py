"""Bounded segment/interval replay from the existing immutable backtest, without fitting."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sqlite3
import tempfile
from collections import Counter
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.backtest import backtest_code, load_backtest
from retailops_ai.forecasting.backtest_contract import EVALUATION_ROLES, METHODS, BacktestManifest
from retailops_ai.forecasting.evaluation import MAX_LINE
from retailops_ai.forecasting.evaluation_contract import BaselinePrediction
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifest_contract import FileReceipt, LabelPoint, Membership
from retailops_ai.forecasting.manifest_io import MAX_BYTES, dump, iter_table, key
from retailops_ai.forecasting.manifests import feature_key, input_models, verify_feature_set
from retailops_ai.forecasting.model_contract import LEARNED_NAMES, ModelPrediction
from retailops_ai.forecasting.models import load_comparison
from retailops_ai.forecasting.quality_contract import (
    QualityDescriptor,
    QualityManifest,
    QualityPolicy,
    SegmentMetric,
)
from retailops_ai.forecasting.quality_metrics import (
    SegmentAccumulator,
    assess_segment,
    interval_bounds,
    residual_rank,
    volume_bin,
)
from retailops_ai.forecasting.splits import load_split
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.protocol import resource_bytes
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

REPORT_METHODS = (*METHODS, "validation_baseline")
Group = tuple[str, str, str, str, str]
REPORTS = {
    "config.json",
    "segments.json",
    "calibration.json",
    "gates.json",
    "model_card.json",
    "intervals.jsonl",
}


def quality_code() -> dict[str, str]:
    return {
        "forecasting/" + name: hashlib.sha256(
            files("retailops_ai.forecasting").joinpath(name).read_bytes()
        ).hexdigest()
        for name in (
            "quality_contract.py",
            "quality_metrics.py",
            "quality.py",
            "evaluation.py",
            "evaluation_contract.py",
        )
    }


def _index(
    db: sqlite3.Connection,
    feature_dir: Path,
    backtest_dir: Path,
    policy: QualityPolicy,
) -> tuple[BacktestManifest, dict[str, Any], dict[str, tuple[str, ...]]]:
    backtest = load_backtest(backtest_dir)
    if backtest.descriptor.code != backtest_code() or backtest.descriptor.status != "passed":
        raise SnapshotError("quality_requires_passed_backtest_with_current_code_pins")
    feature = verify_feature_set(feature_dir)
    if (
        feature.feature_set_id != backtest.descriptor.feature_set_id
        or feature.descriptor.parent != backtest.descriptor.parent
    ):
        raise SnapshotError("quality_feature_backtest_binding_mismatch")
    split = load_split(backtest_dir / "split")
    comparison = load_comparison(backtest_dir / "comparison")
    choices = {selection.fold: selection for selection in comparison.descriptor.selections}
    size = db.execute("PRAGMA page_size").fetchone()[0]
    db.execute(f"PRAGMA max_page_count={MAX_BYTES // size}")
    db.execute("PRAGMA cache_size=-4096")
    db.execute("PRAGMA temp_store=FILE")
    db.execute(
        "CREATE TABLE features (key BLOB PRIMARY KEY, category TEXT, horizon INTEGER, volume TEXT)"
    )
    db.execute(
        "CREATE TABLE members (key BLOB PRIMARY KEY,fold TEXT,role TEXT,eligible INTEGER,reasons BLOB,feature_key BLOB,body BLOB)"
    )
    db.execute("CREATE TABLE labels (key BLOB PRIMARY KEY,actual INTEGER,available TEXT)")
    db.execute(
        "CREATE TABLE predictions (key BLOB,method TEXT,units REAL, PRIMARY KEY(key,method))"
    )
    for row in input_models(feature_dir, "features"):
        if not isinstance(row, InputRow):
            raise SnapshotError("quality_input_schema_mismatch")
        values = {value.name: value.value for value in row.values}
        historical = values["rolling_mean_28"]
        if historical is not None and type(historical) is not float:
            raise SnapshotError("quality_volume_schema_mismatch")
        db.execute(
            "INSERT INTO features VALUES (?,?,?,?)",
            (
                feature_key(row),
                values["category_id"] or "__missing__",
                row.horizon_days,
                volume_bin(historical, policy),
            ),
        )
    budget: Counter[str] = Counter()
    # Role/grain completeness and logical table hashes were independently checked by load_backtest.
    for record in iter_table(
        backtest_dir / "split", "memberships", split.tables["memberships"], budget
    ):
        if not isinstance(record, Membership):
            raise SnapshotError("quality_membership_schema_mismatch")
        if record.role in EVALUATION_ROLES:
            db.execute(
                "INSERT INTO members VALUES (?,?,?,?,?,?,?)",
                (
                    key(record),
                    record.fold,
                    record.role,
                    int(record.eligible),
                    canonical_bytes(record.reasons),
                    feature_key(record),
                    canonical_bytes(record.model_dump(mode="json")),
                ),
            )
    for record in iter_table(backtest_dir / "split", "labels", split.tables["labels"], budget):
        if not isinstance(record, LabelPoint):
            raise SnapshotError("quality_label_schema_mismatch")
        if record.role in EVALUATION_ROLES:
            db.execute(
                "INSERT INTO labels VALUES (?,?,?)",
                (
                    key(record),
                    record.observed_sales_units,
                    record.label_available_at.isoformat()
                    if record.label_available_at is not None
                    else None,
                ),
            )
    db.execute("CREATE INDEX member_roles ON members(fold,role,eligible)")
    if db.execute(
        "SELECT COUNT(*) FROM members m LEFT JOIN features f ON m.feature_key=f.key WHERE f.key IS NULL"
    ).fetchone()[0]:
        raise SnapshotError("quality_feature_key_missing")
    # Freeze expected categories/channels from memberships/features, without reading label outcomes.
    domains = {
        "global": ("all",),
        "horizon": tuple(str(h) for h in range(1, 15)),
        "category": tuple(
            row[0]
            for row in db.execute(
                "SELECT DISTINCT f.category FROM members m JOIN features f ON m.feature_key=f.key ORDER BY f.category"
            )
        ),
        "channel": tuple(
            sorted({json.loads(body)[5] for (body,) in db.execute("SELECT key FROM members")})
        ),
        "volume": tuple(
            sorted(
                set(policy.required_volume_bins)
                | {
                    row[0]
                    for row in db.execute(
                        "SELECT DISTINCT f.volume FROM members m JOIN features f ON m.feature_key=f.key"
                    )
                }
            )
        ),
    }
    with regular_file(backtest_dir / "comparison", "predictions.jsonl") as stream:
        while line := stream.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE or not line.endswith(b"\n"):
                raise SnapshotError("quality_prediction_line_limit")
            payload = decode_json(line)
            pred = (
                ModelPrediction.model_validate_json(line)
                if payload["model"] in LEARNED_NAMES
                else BaselinePrediction.model_validate_json(line)
            )
            if pred.role not in EVALUATION_ROLES:
                continue
            units = (
                pred.predicted_units
                if isinstance(pred, ModelPrediction)
                else pred.estimate.predicted_units
                if pred.estimate
                else None
            )
            aliases: list[str] = [pred.model]
            choice = choices[pred.fold]
            if pred.model == choice.selected:
                aliases.append("validation_selected")
            if pred.model == choice.baseline:
                aliases.append("validation_baseline")
            for method in aliases:
                db.execute("INSERT INTO predictions VALUES (?,?,?)", (key(pred), method, units))
    if db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] != db.execute(
        "SELECT COUNT(*) FROM members"
    ).fetchone()[0] * len(REPORT_METHODS):
        raise SnapshotError("quality_prediction_method_coverage_mismatch")
    db.commit()
    return backtest, choices, domains


def _calibrate(
    db: sqlite3.Connection, backtest: BacktestManifest, policy: QualityPolicy
) -> list[dict[str, Any]]:
    result = []
    for audit in backtest.descriptor.folds:
        fold = audit.plan
        for method in REPORT_METHODS:
            for horizon in range(1, 15):
                args = (fold.name, method, horizon)
                query = """FROM predictions p JOIN members m ON p.key=m.key
                    JOIN labels l ON m.key=l.key JOIN features f ON m.feature_key=f.key
                    WHERE m.fold=? AND p.method=? AND f.horizon=?
                    AND m.role='validation' AND m.eligible=1"""
                eligible, predicted, latest = db.execute(
                    "SELECT COUNT(*),COUNT(p.units),MAX(l.available) " + query,
                    args,
                ).fetchone()
                digest = hashlib.sha256()
                for grain, actual, units, available in db.execute(
                    "SELECT m.key,l.actual,p.units,l.available " + query + " ORDER BY m.key",
                    args,
                ):
                    if (
                        actual is None
                        or available is None
                        or datetime.fromisoformat(available) > fold.selection_cutoff
                    ):
                        raise SnapshotError("quality_calibration_label_unavailable_at_selection")
                    digest.update(
                        canonical_bytes([json.loads(grain), actual, units, available]) + b"\n"
                    )
                rank = residual_rank(eligible, policy) if predicted == eligible else None
                quantile = None
                if rank is not None:
                    quantile = float(
                        db.execute(
                            "SELECT ABS(l.actual-p.units) "
                            + query
                            + " ORDER BY ABS(l.actual-p.units),m.key LIMIT 1 OFFSET ?",
                            (*args, rank - 1),
                        ).fetchone()[0]
                    )
                body = {
                    "fold": fold.name,
                    "method": method,
                    "horizon_days": horizon,
                    "calibration_role": "validation",
                    "eligible_rows": eligible,
                    "predicted_rows": predicted,
                    "rank": rank,
                    "residual_quantile_units": quantile,
                    "nominal_coverage": policy.nominal_coverage,
                    "calibration_cutoff": fold.selection_cutoff.isoformat(),
                    "latest_label_available_at": latest,
                    "source_rows_sha256": digest.hexdigest(),
                    "status": "passed" if quantile is not None else "not_ready",
                    "not_ready_reason": None
                    if quantile is not None
                    else "insufficient_calibration_or_missing_prediction",
                }
                result.append(
                    {
                        **body,
                        "calibration_id": "forecast-calibration-sha256-" + canonical_sha256(body),
                    }
                )
    return result


def _score(
    root: Path,
    db: sqlite3.Connection,
    backtest: BacktestManifest,
    policy: QualityPolicy,
    domains: dict[str, tuple[str, ...]],
    calibration: list[dict[str, Any]],
) -> tuple[list[SegmentMetric], int]:
    cells: dict[Group, SegmentAccumulator] = {}
    for fold in (*[audit.plan.name for audit in backtest.descriptor.folds], "pooled"):
        for role in EVALUATION_ROLES:
            for method in REPORT_METHODS:
                for dimension, values in domains.items():
                    for value in values:
                        if len(cells) >= policy.max_groups:
                            raise SnapshotError("quality_segment_group_limit")
                        cells[(fold, role, method, dimension, value)] = SegmentAccumulator(
                            policy.nominal_coverage
                        )
    lookup = {(c["fold"], c["method"], c["horizon_days"]): c for c in calibration}
    rows = size = 0
    with (root / "intervals.jsonl").open("xb") as stream:
        for (
            _grain,
            fold,
            role,
            eligible,
            reasons_raw,
            body,
            category,
            horizon,
            volume,
            actual,
            method,
            units,
        ) in db.execute("""
            SELECT m.key,m.fold,m.role,m.eligible,m.reasons,m.body,f.category,f.horizon,f.volume,l.actual,p.method,p.units
            FROM members m JOIN features f ON m.feature_key=f.key JOIN labels l ON m.key=l.key
            JOIN predictions p ON m.key=p.key ORDER BY m.key,p.method"""):
            member = json.loads(body)
            reasons = tuple(json.loads(reasons_raw))
            c = lookup[(fold, method, horizon)]
            band = (
                interval_bounds(units, c["residual_quantile_units"])
                if eligible and units is not None
                else None
            )
            dimensions = (
                ("global", "all"),
                ("horizon", str(horizon)),
                ("category", category),
                ("channel", member["channel"]),
                ("volume", volume),
            )
            for scope in (fold, "pooled"):
                for dimension, value in dimensions:
                    cells[(scope, role, method, dimension, value)].add(actual, units, reasons, band)
            if eligible:
                record = {
                    **{
                        k: member[k]
                        for k in (
                            "forecast_origin",
                            "product_id",
                            "selling_location_id",
                            "channel",
                            "target_date",
                            "horizon_days",
                            "business_timezone",
                            "cutoff_policy",
                        )
                    },
                    "fold": fold,
                    "role": role,
                    "method": method,
                    "target_type": "observed_sales_units",
                    "predicted_units": units,
                    "lower_units": band[0] if band else None,
                    "upper_units": band[1] if band else None,
                    "nominal_coverage": policy.nominal_coverage,
                    "calibration_id": c["calibration_id"],
                    "calibration_cutoff": c["calibration_cutoff"],
                    "status": "passed" if band else "not_ready",
                    "evaluation_use": "in_sample_calibration_diagnostic"
                    if role == "validation"
                    else "out_of_time_development_diagnostic",
                }
                raw = canonical_bytes(record) + b"\n"
                size += len(raw)
                rows += 1
                if size > MAX_BYTES or rows > 3000000:
                    raise SnapshotError("quality_interval_resource_limit")
                stream.write(raw)
    metrics = [cells[group].result(*group) for group in sorted(cells)]
    # The new statistics extend the original evaluator; the point metric must agree.
    for metric in metrics:
        if (
            metric.fold != "pooled"
            or metric.dimension != "global"
            or metric.method == "validation_baseline"
        ):
            continue
        old = backtest.descriptor.pooled_metrics[metric.role + ":" + metric.method]
        if (
            metric.point.eligible_rows != old.eligible_rows
            or metric.point.predicted_rows != old.predicted_rows
            or metric.point.status != old.status
        ):
            raise SnapshotError("quality_shared_evaluator_count_mismatch")
        for name in ("mae", "wape", "absolute_error_sum", "absolute_actual_sum"):
            new_value, old_value = getattr(metric.point, name), getattr(old, name)
            if (
                (new_value is None) != (old_value is None)
                or new_value is not None
                and old_value is not None
                and not math.isclose(new_value, old_value, rel_tol=1e-12, abs_tol=1e-12)
            ):
                raise SnapshotError("quality_shared_evaluator_metric_mismatch")
    return metrics, rows


def _gates(
    metrics: list[SegmentMetric], choices: dict[str, Any], policy: QualityPolicy
) -> list[dict[str, Any]]:
    index: dict[Group, SegmentMetric] = {
        (m.fold, m.role, m.method, m.dimension, m.value): m for m in metrics
    }
    gates = []
    for metric in metrics:
        if metric.method != "validation_selected":
            continue
        retained = (
            all(c.selected == c.baseline for c in choices.values())
            if metric.fold == "pooled"
            else choices[metric.fold].selected == choices[metric.fold].baseline
        )
        baseline = index[
            (metric.fold, metric.role, "validation_baseline", metric.dimension, metric.value)
        ]
        gates.append(assess_segment(metric, baseline, policy, retained=retained))
    return gates


def _json_report(root: Path, name: str, body: Any) -> None:
    if isinstance(body, list):
        body = {"rows": body}
    raw = canonical_bytes(body) + b"\n"
    decode_json(raw)  # Metadata retain the existing 4 MiB cap, including generated reports.
    (root / name).write_bytes(raw)


def _assemble(
    root: Path, feature_dir: Path, backtest_dir: Path, policy: QualityPolicy
) -> QualityManifest:
    code = quality_code()
    with tempfile.TemporaryDirectory(prefix="forecast-quality-index-") as temporary:
        with sqlite3.connect(Path(temporary) / "quality.sqlite") as db:
            backtest, choices, domains = _index(db, feature_dir, backtest_dir, policy)
            calibration = _calibrate(
                db, backtest, policy
            )  # Validation only, before scoring holdouts.
            metrics, rows = _score(root, db, backtest, policy, domains, calibration)
            gates = _gates(metrics, choices, policy)
    counts = Counter(g["status"] for g in gates)
    status = "not_ready" if counts["not_ready"] else "failed" if counts["failed"] else "passed"
    card = {
        "target_type": "observed_sales_units",
        "forecast_model_status": "not_ready",
        "backtest_id": backtest.backtest_id,
        "quality_status": status,
        "dimensions": domains,
        "bias_sign": "forecast_minus_actual_positive_is_overforecast",
        "interval_assumptions": [
            "validation residuals are reused for model choice and calibration: development evidence only",
            "overlapping horizons and serial outcomes are dependent; no exchangeability or finite-sample coverage guarantee is claimed",
            "validation empirical coverage is an in-sample calibration diagnostic; holdout coverage is out-of-time",
            "historical volume bins use only rolling mean known at origin, never target actuals",
        ],
        "limitations": [
            "previously observed synthetic development data; no commercial effectiveness claim",
            "observed sales can be inventory constrained; inventory/truth excluded, inventory segments deferred until AI06",
            "quality gates do not select a replacement from holdout or activate/promote a model",
            "portfolio final test not included or opened; freeze its protocol separately before AI09",
            "legacy RetailOps RF rejection remains unchanged",
        ],
    }
    for name, body in (
        ("config.json", policy.model_dump(mode="json")),
        ("segments.json", [m.model_dump(mode="json") for m in metrics]),
        ("calibration.json", calibration),
        ("gates.json", gates),
        ("model_card.json", card),
    ):
        _json_report(root, name, body)
    receipts = {}
    for name in sorted(REPORTS):
        size, digest = file_hash(root, name)
        receipts[name] = FileReceipt(
            path=name,
            size_bytes=size,
            sha256=digest,
            row_count=rows if name == "intervals.jsonl" else 1,
        )
    descriptor = QualityDescriptor.model_validate(
        {
            "backtest_id": backtest.backtest_id,
            "backtest_descriptor_sha256": canonical_sha256(
                backtest.descriptor.model_dump(mode="json")
            ),
            "feature_set_id": backtest.descriptor.feature_set_id,
            "policy": policy,
            "code_files": code,
            "code_sha256": canonical_sha256(code),
            "dependency_lock_sha256": hashlib.sha256(
                resource_bytes("dependencies.lock")
            ).hexdigest(),
            "python_version": platform.python_version(),
            "quality_status": status,
            "gate_counts": {name: counts[name] for name in ("passed", "failed", "not_ready")},
            "calibration_status_counts": dict(Counter(c["status"] for c in calibration)),
            "segment_count": len(metrics),
            "interval_rows": rows,
            "report_sha256": {name: r.sha256 for name, r in receipts.items()},
        }
    )
    manifest = QualityManifest(
        quality_id="forecast-quality-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
        receipts=receipts,
        generated_at=datetime.now(UTC),
    )
    if quality_code() != code:
        raise SnapshotError("quality_implementation_changed_during_run")
    dump(root / "quality_manifest.json", manifest)
    return manifest


def load_quality(root: Path) -> QualityManifest:
    """Check artifact integrity. Semantic verification requires parents and verify_quality."""
    checked_directory(root)
    raw = read_bytes(root, "quality_manifest.json")
    decode_json(raw)
    manifest = QualityManifest.model_validate_json(raw)
    if set(manifest.receipts) != REPORTS:
        raise SnapshotError("quality_report_inventory_mismatch")
    for name, receipt in manifest.receipts.items():
        if file_hash(root, name) != (receipt.size_bytes, receipt.sha256):
            raise SnapshotError("quality_report_checksum_mismatch")
    config = read_bytes(root, "config.json")
    if decode_json(config) != manifest.descriptor.policy.model_dump(mode="json"):
        raise SnapshotError("quality_config_binding_mismatch")
    raw_metrics = decode_json(read_bytes(root, "segments.json"))["rows"]
    if not isinstance(raw_metrics, list):
        raise SnapshotError("quality_segments_array_required")
    metrics = [SegmentMetric.model_validate_json(canonical_bytes(m)) for m in raw_metrics]
    if len(metrics) != manifest.descriptor.segment_count:
        raise SnapshotError("quality_segment_count_mismatch")
    gates = decode_json(read_bytes(root, "gates.json"))["rows"]
    counts = Counter(g["status"] for g in gates)
    if {
        name: counts[name] for name in ("passed", "failed", "not_ready")
    } != manifest.descriptor.gate_counts:
        raise SnapshotError("quality_gate_receipt_mismatch")
    if manifest.receipts["intervals.jsonl"].row_count != manifest.descriptor.interval_rows:
        raise SnapshotError("quality_interval_receipt_mismatch")
    inventory(root, REPORTS | {"quality_manifest.json"})
    return manifest


def build_quality(
    feature_dir: Path, backtest_dir: Path, output_root: Path, policy: QualityPolicy | None = None
) -> Path:
    policy = (
        QualityPolicy()
        if policy is None
        else QualityPolicy.model_validate_json(policy.model_dump_json())
    )
    output_root = output_root.absolute()
    if any(output_root.is_relative_to(p.absolute()) for p in (feature_dir, backtest_dir)):
        raise SnapshotError("quality_output_inside_input")
    output_root.mkdir(parents=True, exist_ok=True)
    checked_directory(output_root)
    with tempfile.TemporaryDirectory(prefix=".quality-", dir=output_root) as temporary:
        root = Path(temporary)
        manifest = _assemble(root, feature_dir, backtest_dir, policy)
        load_quality(root)
        fsync_tree(root)
        destination = output_root / manifest.quality_id
        try:
            publish_noreplace(root, destination)
        except FileExistsError:
            if load_quality(destination).descriptor != manifest.descriptor:
                raise SnapshotError("quality_immutable_publication_conflict") from None
        return destination


def verify_quality(root: Path, feature_dir: Path, backtest_dir: Path) -> QualityManifest:
    manifest = load_quality(root)
    if manifest.descriptor.code_files != quality_code():
        raise SnapshotError("quality_verification_code_pin_mismatch")
    with tempfile.TemporaryDirectory(prefix="forecast-quality-verify-") as temporary:
        expected = _assemble(
            Path(temporary).resolve(), feature_dir, backtest_dir, manifest.descriptor.policy
        )
        if expected.descriptor != manifest.descriptor:
            raise SnapshotError("quality_source_replay_mismatch")
    return manifest
