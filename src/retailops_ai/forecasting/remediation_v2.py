"""Validation-only segmented postprocessing, honest fallback and out-of-time quality gates."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from itertools import groupby
from pathlib import Path
from typing import Any, cast

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.evaluation_contract import BASELINES
from retailops_ai.forecasting.manifest_contract import FileReceipt
from retailops_ai.forecasting.model_contract import LEARNED_NAMES
from retailops_ai.forecasting.quality import _index
from retailops_ai.forecasting.quality_contract import QualityStatus, SegmentMetric
from retailops_ai.forecasting.quality_metrics import SegmentAccumulator, assess_segment
from retailops_ai.forecasting.remediation_v2_contract import (
    RemediationDescriptor,
    RemediationManifest,
    RemediationPolicy,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_bytes,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

METHODS = (*BASELINES, *LEARNED_NAMES)
SCORED = ("remediated", "validation_baseline", "validation_selected")
REPORTS = {
    "config.json",
    "recipe.json",
    "calibration.json",
    "segments.json",
    "gates.json",
    "predictions.jsonl",
    "model_card.json",
}


@dataclass(frozen=True)
class Observation:
    key: bytes
    fold: str
    role: str
    member: dict[str, Any]
    category: str
    volume: str
    actual: int | None
    available: str | None
    predictions: dict[str, float | None]

    @property
    def group(self) -> tuple[str, str, str]:
        return self.fold, self.volume, self.category


def observations(db: sqlite3.Connection, *, validation_only: bool = False) -> Iterator[Observation]:
    query = """SELECT m.key,m.fold,m.role,m.body,f.category,f.volume,l.actual,l.available,
                   p.method,p.units FROM members m JOIN features f ON m.feature_key=f.key
                   JOIN labels l ON m.key=l.key JOIN predictions p ON m.key=p.key
                   WHERE (?=0 OR (m.role='validation' AND m.eligible=1)) ORDER BY m.key,p.method"""
    for grain, grouped in groupby(
        db.execute(query, (int(validation_only),)), key=lambda row: row[0]
    ):
        rows = list(grouped)
        first = rows[0]
        yield Observation(
            grain,
            first[1],
            first[2],
            json.loads(first[3]),
            first[4],
            first[5],
            first[6],
            first[7],
            {r[8]: r[9] for r in rows},
        )


def source_hash(rows: list[Observation]) -> str:
    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda r: r.key):
        digest.update(
            canonical_bytes([json.loads(row.key), row.actual, row.available, row.predictions])
            + b"\n"
        )
    return digest.hexdigest()


def corrected(units: float, correction: dict[str, Any]) -> float:
    return max(0.0, units * float(correction["ratio"]) + float(correction["offset"]))


def candidate_units(row: Observation, candidate: dict[str, Any], baseline: str) -> float:
    weight = float(candidate["weight"])
    raw = corrected(
        float(cast(float, row.predictions[candidate["method"]])), candidate["correction"]
    )
    return weight * raw + (1 - weight) * float(cast(float, row.predictions[baseline]))


def candidate_metrics(
    rows: list[Observation], candidate: dict[str, Any], baseline: str
) -> dict[str, Any]:
    actual = math.fsum(float(cast(int, row.actual)) for row in rows)
    predicted = [candidate_units(row, candidate, baseline) for row in rows]
    return {
        "mae": math.fsum(
            abs(float(cast(int, row.actual)) - units)
            for row, units in zip(rows, predicted, strict=True)
        )
        / len(rows),
        "normalized_bias": (math.fsum(predicted) - actual) / actual if actual else None,
    }


def choose_candidate(
    training: list[Observation], held: list[Observation], baseline: str, policy: RemediationPolicy
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """A predeclared grid, fitted on the first block and guarded on both blocks."""
    identity = {"method": baseline, "weight": 1.0, "correction": {"ratio": 1.0, "offset": 0.0}}
    reference = [candidate_metrics(block, identity, baseline) for block in (training, held)]
    options: list[dict[str, Any]] = []
    for method in METHODS:
        if any(row.predictions[method] is None for row in (*training, *held)):
            continue
        actual = math.fsum(float(cast(int, row.actual)) for row in training)
        expected = math.fsum(float(cast(float, row.predictions[method])) for row in training)
        fitted = {
            "ratio": min(
                policy.maximum_correction_ratio,
                max(policy.minimum_correction_ratio, actual / expected),
            )
            if expected
            else 1.0,
            "offset": 0.0 if expected else actual / len(training),
        }
        for correction in ({"ratio": 1.0, "offset": 0.0}, fitted):
            for weight in (0.25, 0.5, 0.75, 1.0):
                candidate = {"method": method, "weight": weight, "correction": correction}
                blocks = [
                    candidate_metrics(block, candidate, baseline) for block in (training, held)
                ]
                safe_mae = all(
                    m["mae"]
                    <= ref["mae"]
                    * (1 + policy.quality.maximum_critical_segment_relative_regression)
                    for m, ref in zip(blocks, reference, strict=True)
                )
                safe_bias = all(
                    m["normalized_bias"] is not None
                    and abs(m["normalized_bias"]) <= policy.quality.maximum_absolute_normalized_bias
                    for m in blocks
                )
                options.append(
                    {
                        **candidate,
                        "fit_metrics": blocks[0],
                        "selection_metrics": blocks[1],
                        "passes_mae_guard": safe_mae,
                        "passes_bias_guard": safe_bias,
                    }
                )
    acceptable = [c for c in options if c["passes_mae_guard"] and c["passes_bias_guard"]]
    best = (
        min(
            acceptable,
            key=lambda c: (c["selection_metrics"]["mae"], c["weight"], METHODS.index(c["method"])),
        )
        if acceptable
        else None
    )
    baseline_bias = reference[1]["normalized_bias"]
    use = best is not None and (
        best["selection_metrics"]["mae"]
        < reference[1]["mae"] * (1 - policy.quality.minimum_relative_improvement)
        or (
            baseline_bias is not None
            and abs(baseline_bias) > policy.quality.maximum_absolute_normalized_bias
        )
    )
    return (best if use and best is not None else identity), options, use


def predict(
    row: Observation, recipe: dict[tuple[str, str, str], dict[str, Any]], choices: dict[str, Any]
) -> float | None:
    if not row.member["eligible"]:
        return None
    cell = recipe.get(row.group)
    method = cell["selected"] if cell else choices[row.fold].baseline
    units = row.predictions[method]
    if units is None:
        return None
    if cell and cell["corrected"]:
        return candidate_units(
            row,
            {"method": method, "weight": cell["weight"], "correction": cell["correction"]},
            cell["baseline"],
        )
    return units


def fit_recipe(
    db: sqlite3.Connection, backtest: Any, choices: dict[str, Any], policy: RemediationPolicy
) -> tuple[dict[tuple[str, str, str], dict[str, Any]], list[Observation]]:
    """Only validation outcomes enter fitting, selection or calibration."""
    rows = list(observations(db, validation_only=True))
    if len(rows) > 300000:
        raise SnapshotError("remediation_validation_row_budget")
    folds = {a.plan.name: a.plan for a in backtest.descriptor.folds}
    by_fold: dict[str, list[Observation]] = defaultdict(list)
    for row in rows:
        cutoff = folds[row.fold].selection_cutoff
        if (
            row.actual is None
            or row.available is None
            or datetime.fromisoformat(row.available) > cutoff
        ):
            raise SnapshotError("remediation_validation_label_unavailable_at_selection")
        by_fold[row.fold].append(row)
    recipe = {}
    calibration_rows = []
    for fold, fold_rows in sorted(by_fold.items()):
        origins = sorted({r.member["forecast_origin"] for r in fold_rows})
        if len(origins) < 2:
            raise SnapshotError("remediation_requires_two_validation_origin_blocks")
        boundary = origins[len(origins) // 2]
        fit = [r for r in fold_rows if r.member["forecast_origin"] < boundary]
        selection = [r for r in fold_rows if r.member["forecast_origin"] >= boundary]
        calibration_rows.extend(selection)
        for group in sorted({r.group for r in fold_rows}):
            training = [r for r in fit if r.group == group]
            held = [r for r in selection if r.group == group]
            baseline = choices[fold].baseline
            ready = (
                len(training) >= policy.minimum_fit_rows
                and len(held) >= policy.minimum_selection_rows
            )
            chosen = {
                "method": baseline,
                "weight": 1.0,
                "correction": {"ratio": 1.0, "offset": 0.0},
            }
            options: list[dict[str, Any]] = []
            use = False
            if ready:
                chosen, options, use = choose_candidate(training, held, baseline, policy)
            baseline_mae = (
                candidate_metrics(
                    held,
                    {
                        "method": baseline,
                        "weight": 1.0,
                        "correction": {"ratio": 1.0, "offset": 0.0},
                    },
                    baseline,
                )["mae"]
                if held
                else None
            )
            recipe[group] = {
                "fold": fold,
                "volume": group[1],
                "category": group[2],
                "selected": chosen["method"],
                "weight": chosen["weight"],
                "corrected": use,
                "correction": chosen["correction"],
                "baseline": baseline,
                "baseline_selection_mae": baseline_mae,
                "candidates": options,
                "fit_rows": len(training),
                "selection_rows": len(held),
                "fit_last_origin": max(
                    (r.member["forecast_origin"] for r in training), default=None
                ),
                "selection_first_origin": boundary,
                "selection_cutoff": folds[fold].selection_cutoff.isoformat(),
                "fit_source_sha256": source_hash(training),
                "selection_source_sha256": source_hash(held),
                "status": "passed" if ready else "not_ready",
                "reason": None
                if ready
                else "insufficient_validation_group_sample_baseline_retained",
            }
    return recipe, calibration_rows


def residual_quantiles(
    residuals: list[float], policy: RemediationPolicy
) -> tuple[float, float] | None:
    n = len(residuals)
    if policy.calibration == "second_half_validation_signed_residual_equal_tail_quantiles":
        alpha = (1 - policy.quality.nominal_coverage) / 2
        lo, hi = math.floor((n + 1) * alpha), math.ceil((n + 1) * (1 - alpha))
        if n < policy.quality.minimum_calibration_rows or not 1 <= lo <= hi <= n:
            return None
        ordered = sorted(residuals)
        return ordered[lo - 1], ordered[hi - 1]
    rank = math.ceil((n + 1) * policy.quality.nominal_coverage)
    if n < policy.quality.minimum_calibration_rows or not 1 <= rank <= n:
        return None
    ordered = sorted(residuals)
    # Every interval contains at least the unchanged nominal rank. Choice uses
    # calibration only; no assumption of conditional or out-of-time coverage.
    windows = ((ordered[i], ordered[i + rank - 1]) for i in range(n - rank + 1))
    return min(windows, key=lambda bounds: (max(0.0, bounds[1]) - min(0.0, bounds[0]), bounds))


def residual_scale(units: float) -> float:
    return max(0.5, math.sqrt(units))


def calibrate(
    rows: list[Observation],
    recipe: dict[tuple[str, str, str], dict[str, Any]],
    choices: dict[str, Any],
    policy: RemediationPolicy,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    pools: dict[tuple[str, str, str, str], list[tuple[Observation, float]]] = defaultdict(list)
    scaled = policy.calibration == "second_half_scaled_shortest_nominal_residual_window"
    for row in rows:
        for method in SCORED:
            units = (
                predict(row, recipe, choices) if method == "remediated" else row.predictions[method]
            )
            if units is None or row.actual is None:
                raise SnapshotError("remediation_calibration_prediction_missing")
            for volume, category in ((row.volume, row.category), (row.volume, "*"), ("*", "*")):
                pools[row.fold, method, volume, category].append(
                    (row, (row.actual - units) / (residual_scale(units) if scaled else 1.0))
                )
    result = {}
    for key, sample in sorted(pools.items()):
        quantiles = residual_quantiles([residual for _, residual in sample], policy)
        body = {
            "fold": key[0],
            "method": key[1],
            "volume": key[2],
            "category": key[3],
            "rows": len(sample),
            "residual_scale": "max_0_5_sqrt_prediction" if scaled else "identity",
            "window": "shortest_contains_point_nominal_n_plus_1_rank"
            if scaled
            else "equal_tail_nominal_n_plus_1_ranks",
            "lower_residual": quantiles[0] if quantiles else None,
            "upper_residual": quantiles[1] if quantiles else None,
            "source_rows_sha256": source_hash([row for row, _ in sample]),
            "latest_label_available_at": max(cast(str, row.available) for row, _ in sample),
            "status": "passed" if quantiles else "not_ready",
        }
        result[key] = {
            **body,
            "calibration_id": "forecast-calibration-sha256-" + canonical_sha256(body),
        }
    return result


def interval(
    row: Observation,
    method: str,
    units: float | None,
    calibration: dict[tuple[str, str, str, str], dict[str, Any]],
) -> tuple[tuple[float, float] | None, dict[str, Any] | None]:
    if units is None:
        return None, None
    for volume, category in ((row.volume, row.category), (row.volume, "*"), ("*", "*")):
        cell = calibration.get((row.fold, method, volume, category))
        if cell and cell["status"] == "passed":
            scale = (
                residual_scale(units)
                if cell["residual_scale"] == "max_0_5_sqrt_prediction"
                else 1.0
            )
            return (
                max(0.0, min(units, units + cell["lower_residual"] * scale)),
                max(units, units + cell["upper_residual"] * scale),
            ), cell
    return None, None


def score(
    root: Path,
    db: sqlite3.Connection,
    backtest: Any,
    choices: dict[str, Any],
    domains: dict[str, tuple[str, ...]],
    policy: RemediationPolicy,
    recipe: dict[tuple[str, str, str], dict[str, Any]],
    calibration: dict[tuple[str, str, str, str], dict[str, Any]],
) -> tuple[list[SegmentMetric], int]:
    cells: dict[tuple[str, str, str, str, str], SegmentAccumulator] = {}
    for fold in (*[a.plan.name for a in backtest.descriptor.folds], "pooled"):
        for role in ("validation", "development_holdout"):
            for method in SCORED:
                for dimension, values in domains.items():
                    for value in values:
                        if len(cells) >= policy.quality.max_groups:
                            raise SnapshotError("remediation_segment_group_budget")
                        cells[fold, role, method, dimension, value] = SegmentAccumulator(
                            policy.quality.nominal_coverage
                        )
    rows = size = 0
    with (root / "predictions.jsonl").open("xb") as stream:
        for row in observations(db):
            for method in SCORED:
                units = (
                    predict(row, recipe, choices)
                    if method == "remediated"
                    else row.predictions[method]
                )
                band, calibration_cell = interval(row, method, units, calibration)
                dimensions = (
                    ("global", "all"),
                    ("horizon", str(row.member["horizon_days"])),
                    ("category", row.category),
                    ("channel", row.member["channel"]),
                    ("volume", row.volume),
                )
                for fold in (row.fold, "pooled"):
                    for dimension, value in dimensions:
                        cells[fold, row.role, method, dimension, value].add(
                            row.actual, units, tuple(row.member["reasons"]), band
                        )
                record = {
                    "key": json.loads(row.key),
                    "method": method,
                    "eligible": row.member["eligible"],
                    "reasons": row.member["reasons"],
                    "predicted_units": units,
                    "lower_units": band[0] if band else None,
                    "upper_units": band[1] if band else None,
                    "calibration_id": calibration_cell["calibration_id"]
                    if calibration_cell
                    else None,
                    "calibration_group": [calibration_cell["volume"], calibration_cell["category"]]
                    if calibration_cell
                    else None,
                }
                raw = canonical_bytes(record) + b"\n"
                rows += 1
                size += len(raw)
                if rows > 3000000 or size > 2 * 1024**3:
                    raise SnapshotError("remediation_prediction_budget")
                stream.write(raw)
    return [cells[key].result(*key) for key in sorted(cells)], rows


def code_files() -> dict[str, str]:
    return {
        "forecasting/" + name: hashlib.sha256(
            files("retailops_ai.forecasting").joinpath(name).read_bytes()
        ).hexdigest()
        for name in (
            "remediation_v2.py",
            "remediation_v2_contract.py",
            "quality.py",
            "quality_contract.py",
            "quality_metrics.py",
        )
    }


def assemble(
    root: Path, features: Path, backtests: Path, policy: RemediationPolicy
) -> RemediationManifest:
    code = code_files()
    with tempfile.TemporaryDirectory(prefix="forecast-remediation-index-") as temporary:
        with sqlite3.connect(Path(temporary) / "index.sqlite") as db:
            parent, choices, domains = _index(db, features, backtests, policy.quality)
            recipe, sample = fit_recipe(db, parent, choices, policy)
            calibration = calibrate(sample, recipe, choices, policy)
            metrics, rows = score(root, db, parent, choices, domains, policy, recipe, calibration)
    index = {(m.fold, m.role, m.method, m.dimension, m.value): m for m in metrics}
    gates = []
    for metric in metrics:
        if metric.method != "remediated":
            continue
        relevant = [
            cell
            for cell in recipe.values()
            if metric.fold == "pooled" or cell["fold"] == metric.fold
        ]
        retained = all(
            not cell["corrected"] and cell["selected"] == cell["baseline"] for cell in relevant
        )
        baseline = index[
            metric.fold, metric.role, "validation_baseline", metric.dimension, metric.value
        ]
        gates.append(assess_segment(metric, baseline, policy.quality, retained=retained))
    # Insufficient fitting samples cannot disappear behind a fallback that happens to score well.
    for cell in recipe.values():
        if cell["status"] == "not_ready":
            gates.append(
                {
                    "fold": cell["fold"],
                    "role": "validation",
                    "dimension": "recipe_sample",
                    "value": cell["volume"] + ":" + cell["category"],
                    "status": "not_ready",
                    "not_ready_reasons": [cell["reason"]],
                    "failed_reasons": [],
                }
            )
    counts = Counter(g["status"] for g in gates)
    status: QualityStatus = (
        "not_ready" if counts["not_ready"] else "failed" if counts["failed"] else "passed"
    )
    reports = {
        "config.json": policy.model_dump(mode="json"),
        "recipe.json": {"rows": list(recipe.values())},
        "calibration.json": {"rows": list(calibration.values())},
        "segments.json": {"rows": [m.model_dump(mode="json") for m in metrics]},
        "gates.json": {"rows": gates},
        "model_card.json": {
            "target_type": "observed_sales_units",
            "quality_status": status,
            "intended_use": "development_quality_remediation_only",
            "forecast_model_status": "not_ready",
            "limitations": [
                "synthetic development evidence; no commercial effectiveness claim",
                "validation-only guarded blends; interval recipe declared in versioned policy",
                "selection and calibration share the second validation block",
                "serial outcomes and overlapping horizons; no coverage guarantee",
                "all original zero-denominator and sample gates retained",
                "no final test access, registry decision, promotion or serving",
            ],
            "backtest_id": parent.backtest_id,
            "feature_set_id": parent.descriptor.feature_set_id,
        },
    }
    for name, body in reports.items():
        raw = canonical_bytes(body) + b"\n"
        decode_json(raw)
        (root / name).write_bytes(raw)
    receipts = {}
    for name in sorted(REPORTS):
        size, digest = file_hash(root, name)
        receipts[name] = FileReceipt(
            path=name,
            size_bytes=size,
            sha256=digest,
            row_count=rows if name == "predictions.jsonl" else 1,
        )
    descriptor = RemediationDescriptor(
        backtest_id=parent.backtest_id,
        feature_set_id=parent.descriptor.feature_set_id,
        policy=policy,
        code_files=code,
        code_sha256=canonical_sha256(code),
        quality_status=status,
        gate_counts={s: counts[s] for s in ("passed", "failed", "not_ready")},
        report_sha256={name: r.sha256 for name, r in receipts.items()},
    )
    if code != code_files():
        raise SnapshotError("remediation_code_changed_during_run")
    manifest = RemediationManifest(
        remediation_id="forecast-remediation-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
        receipts=receipts,
        generated_at=datetime.now(UTC),
    )
    (root / "remediation_manifest.json").write_bytes(
        canonical_bytes(manifest.model_dump(mode="json")) + b"\n"
    )
    return manifest


def load_remediation(root: Path) -> RemediationManifest:
    checked_directory(root)
    manifest = RemediationManifest.model_validate_json(
        read_bytes(root, "remediation_manifest.json")
    )
    if set(manifest.receipts) != REPORTS:
        raise SnapshotError("remediation_report_inventory")
    for name, receipt in manifest.receipts.items():
        if file_hash(root, name) != (receipt.size_bytes, receipt.sha256):
            raise SnapshotError("remediation_report_checksum_mismatch")
    if decode_json(read_bytes(root, "config.json")) != manifest.descriptor.policy.model_dump(
        mode="json"
    ):
        raise SnapshotError("remediation_config_mismatch")
    segments = decode_json(read_bytes(root, "segments.json"))["rows"]
    for segment in segments:
        SegmentMetric.model_validate(segment)
    gates = decode_json(read_bytes(root, "gates.json"))["rows"]
    counts = Counter(g["status"] for g in gates)
    if {s: counts[s] for s in ("passed", "failed", "not_ready")} != manifest.descriptor.gate_counts:
        raise SnapshotError("remediation_gate_count_mismatch")
    inventory(root, REPORTS | {"remediation_manifest.json"})
    return manifest


def build_remediation(
    features: Path, backtests: Path, output_root: Path, policy: RemediationPolicy | None = None
) -> Path:
    policy = (
        RemediationPolicy()
        if policy is None
        else RemediationPolicy.model_validate_json(policy.model_dump_json())
    )
    output_root = output_root.absolute()
    if any(output_root.is_relative_to(p.absolute()) for p in (features, backtests)):
        raise SnapshotError("remediation_output_inside_input")
    output_root.mkdir(parents=True, exist_ok=True)
    checked_directory(output_root)
    with tempfile.TemporaryDirectory(prefix=".remediation-", dir=output_root) as temporary:
        root = Path(temporary)
        manifest = assemble(root, features, backtests, policy)
        load_remediation(root)
        fsync_tree(root)
        destination = output_root / manifest.remediation_id
        try:
            publish_noreplace(root, destination)
        except FileExistsError:
            if load_remediation(destination).descriptor != manifest.descriptor:
                raise SnapshotError("remediation_immutable_conflict") from None
        return destination


def verify_remediation(root: Path, features: Path, backtests: Path) -> RemediationManifest:
    manifest = load_remediation(root)
    if manifest.descriptor.code_files != code_files():
        raise SnapshotError("remediation_verification_code_pin_mismatch")
    with tempfile.TemporaryDirectory(prefix="forecast-remediation-verify-") as temporary:
        expected = assemble(
            Path(temporary).resolve(), features, backtests, manifest.descriptor.policy
        )
        if expected.descriptor != manifest.descriptor:
            raise SnapshotError("remediation_source_replay_mismatch")
    return manifest


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Version 2 validation-only quality remediation")
    parser.add_argument("--feature-dir", type=Path, required=True)
    parser.add_argument("--backtest-dir", type=Path, required=True)
    parser.add_argument(
        "--output-root", type=Path, default=Path("data/generated/forecast-remediation")
    )
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    try:
        directory = args.verify or build_remediation(
            args.feature_dir, args.backtest_dir, args.output_root
        )
        manifest = (
            verify_remediation(directory, args.feature_dir, args.backtest_dir)
            if args.verify
            else load_remediation(directory)
        )
        print(
            json.dumps(
                {
                    "directory": str(directory),
                    "remediation_id": manifest.remediation_id,
                    "quality_status": manifest.descriptor.quality_status,
                    "gate_counts": manifest.descriptor.gate_counts,
                    "forecast_model_status": manifest.forecast_model_status,
                },
                sort_keys=True,
            )
        )
        return 0 if manifest.descriptor.quality_status == "passed" else 3
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "invalid_input", "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
