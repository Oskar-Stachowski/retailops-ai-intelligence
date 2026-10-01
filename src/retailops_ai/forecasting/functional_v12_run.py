"""Self-contained v12 evidence; qualification requires matching independent replay.

Verified regular payloads use hard links on the same filesystem. Cross-device
copies require a measured space preflight. No source generation or model fitting
is imported by this exporter; all original inputs remain in place.
"""

from __future__ import annotations

import errno
import math
import os
import re
import shutil
import stat
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.functional_v12_archive import verify_checkpoint
from retailops_ai.forecasting.functional_v12_campaign import campaign_code, load_campaign
from retailops_ai.forecasting.functional_v12_quality import Dimension, _assess, _Segment
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast, QualityPolicyV2
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    inventory,
    read_json,
    regular_file,
)
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

VERSION = "forecast-functional-v12-run-1.0.0"
MAX_FILES = 20000
MAX_BYTES = 64 * 1024**3
MIN_FREE_BYTES = 1024**3
ROLES = ["validation", "development_holdout"]


def _handoff(manifest: dict[str, Any], proof: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "version": VERSION,
        "campaign_id": manifest["campaign_id"],
        "replay_id": proof["replay_id"],
        "freeze_id": manifest["descriptor"]["freeze"]["freeze_id"],
        "forecast_model_status": status,
        "quality_qualification_status": manifest["descriptor"]["quality_qualification_status"],
        "independent_replay": "passed",
        "all_preregistered_cohorts_included": True,
        "source_regeneration": False,
        "model_refits": 0,
        "deployment": "not_promoted",
        "mlflow_or_registry_written": False,
        "portfolio_final_test": "not_included_not_opened",
        "legacy_results_reclassified": False,
        "model_card": "model_card.json",
        "signature": "signature.json",
        "input_example": "input_example.json",
        "ai05_import": {
            "legacy_single_point_import_compatible": False,
            "status": "requires_v12_campaign_and_dual_target_adapter",
            "preserve": ["run_id", "campaign_id", "freeze_id", "replay_id", "all_file_checksums"],
            "imported_at": "record_separately_from_original_export_and_training_times",
            "promotion": "separate_review_and_serving_qualification_required",
        },
    }


def _input_schema(plan: dict[str, Any], recipes: dict[str, Any]) -> dict[str, Any]:
    """Describe the existing compact adapter, excluding evaluation labels from inference."""
    number = {"type": ["number", "null"], "minimum": 0}
    band = {
        "anyOf": [
            {"type": "null"},
            {
                "type": "array",
                "items": {"type": "number", "minimum": 0},
                "minItems": 2,
                "maxItems": 2,
            },
        ]
    }
    fields = {
        "key": {"type": "string", "minLength": 1},
        "fold": {"enum": [fold["name"] for fold in plan["split_policy"]["folds"]]},
        "role": {"enum": ROLES},
        "origin": {"type": "string", "format": "date-time"},
        "volume": {"enum": plan["required_dimensions"]["volume"]},
        "category": {"enum": plan["required_dimensions"]["category"]},
        "channel": {"enum": plan["required_dimensions"]["channel"]},
        "horizon": {"type": "integer", "minimum": 1, "maximum": 14},
        "eligible": {"type": "boolean"},
        "reasons": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "actual": {"type": "null"},
        "label_available_at": {"type": "null"},
        "baseline_points": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                **{
                    f"{name}:{target}": number
                    for name in ("history7", "history28", "weekday28")
                    for target in ("median", "mean")
                },
                **(
                    {"hgb_mean": number}
                    if plan["method_policy"]["mean_variant"] == "hgb_blend"
                    else {}
                ),
            },
            "required": [
                f"{name}:{target}"
                for name in ("history7", "history28", "weekday28")
                for target in ("median", "mean")
            ],
        },
        "baseline_bands": {
            "type": "object",
            "additionalProperties": False,
            "properties": {name: band for name in ("history7", "history28", "weekday28")},
            "required": ["history7", "history28", "weekday28"],
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cohort_id": {"enum": sorted(recipes)},
            "recipe_id": {
                "enum": sorted(
                    ref["recipe_id"] for value in recipes.values() for ref in value.values()
                )
            },
            "row": {
                "type": "object",
                "additionalProperties": False,
                "properties": fields,
                "required": list(fields),
            },
        },
        "required": ["cohort_id", "recipe_id", "row"],
    }


def _model_reports(campaign: Path, manifest: dict[str, Any], status: str) -> dict[str, Any]:
    """Derive cards from saved metadata only: no compact rows, predictions, labels or fitting."""
    desc = manifest["descriptor"]
    freeze = desc["freeze"]
    plan = freeze["descriptor"]
    recipes = read_json(campaign, "fitted_recipes.json")
    schema = _input_schema(plan, recipes)
    cutoffs = {fold["name"]: fold for fold in plan["split_policy"]["folds"]}
    recipe_index: dict[str, Any] = {}
    for cohort_id, folds in sorted(recipes.items()):
        recipe_index[cohort_id] = {}
        for fold, ref in sorted(folds.items()):
            recipe = read_json(campaign, ref["path"])
            if (
                recipe["recipe_id"] != ref["recipe_id"]
                or recipe["fold"] != fold
                or datetime.fromisoformat(recipe["selection_cutoff"])
                != datetime.fromisoformat(cutoffs[fold]["selection_cutoff"])
                or recipe["policy"] != plan["method_policy"]
            ):
                raise SnapshotError("v12_run_card_recipe_binding")
            recipe_index[cohort_id][fold] = {
                "artifact": {**ref, "path": "campaign/" + ref["path"]},
                "selection_cutoff": recipe["selection_cutoff"],
                "local_recipe_id": recipe["local_recipe_id"],
                "pooled_mean_calibration_id": recipe["pooled_mean_calibration"]["calibration_id"],
                "support": recipe["support"],
                "training_support": recipe["training_support"],
                "calibration_gaps": recipe["calibration_gaps"],
            }
    first_cohort = sorted(recipes)[0]
    first_fold = sorted(recipes[first_cohort])[0]
    first_ref = recipe_index[first_cohort][first_fold]
    day = date.fromisoformat(cutoffs[first_fold]["development_holdout"]["start"])
    origin = day.isoformat() + "T23:59:59+00:00"
    channel = plan["required_dimensions"]["channel"][0]
    points = {
        f"{name}:{target}": 0.0
        for name in ("history7", "history28", "weekday28")
        for target in ("median", "mean")
    }
    if plan["method_policy"]["mean_variant"] == "hgb_blend":
        points["hgb_mean"] = 0.0
    example = {
        "version": "forecast-functional-v12-input-example-1.0.0",
        "purpose": "synthetic_schema_example_not_a_retained_observation_or_performance_measurement",
        "data_rows_read": 0,
        "prediction_executed": False,
        "bound_recipe": first_ref["artifact"],
        "selection_cutoff": first_ref["selection_cutoff"],
        "input": {
            "cohort_id": first_cohort,
            "recipe_id": first_ref["artifact"]["recipe_id"],
            "row": {
                "key": canonical_bytes(
                    [
                        first_fold,
                        "development_holdout",
                        origin,
                        "00000000-0000-0000-0000-000000000001",
                        "00000000-0000-0000-0000-000000000002",
                        channel,
                        (day + timedelta(days=1)).isoformat(),
                    ]
                ).decode(),
                "fold": first_fold,
                "role": "development_holdout",
                "origin": origin,
                "volume": "zero",
                "category": plan["required_dimensions"]["category"][0],
                "channel": channel,
                "horizon": 1,
                "eligible": True,
                "reasons": [],
                "actual": None,
                "label_available_at": None,
                "baseline_points": points,
                "baseline_bands": {
                    name: [0.0, 0.0] for name in ("history7", "history28", "weekday28")
                },
            },
        },
    }
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(example["input"])
    signature = {
        "version": "forecast-functional-v12-signature-1.0.0",
        "target_type": "observed_sales_units",
        "unit": "units",
        "adapter": "functional_v12_cohort.observation_from_compact + functional_v12_recipe.PreparedV12Predictor.predict",
        "input_schema": schema,
        "input_schema_sha256": canonical_sha256(schema),
        "output_schema": FunctionalForecast.model_json_schema(),
        "output_schema_scope": "each_of_candidate_and_baseline_in_returned_three_tuple",
        "return_tuple": [
            "candidate: FunctionalForecast",
            "baseline: FunctionalForecast",
            "metadata: dict",
        ],
        "outputs": {
            "candidate": {
                "median": "MAE target; exact selected local baseline median",
                "mean": "MSE and normalized bias target; frozen mean policy",
                "interval": "central 90%; exact selected local baseline interval",
            },
            "baseline": "separate retained median, mean and interval reference on identical keys",
            "metadata": "recipe_id, calibration cell, mean source and exact-reference flags",
        },
        "preconditions": [
            "verify_run and retained snapshot/compact semantic replay must pass before loading",
            "resolve recipe by both cohort_id and fold; verify its ID and checksum",
            "derive baselines only from histories available_at <= origin, never future actuals",
            "origin closes UTC day at 23:59:59; target_date equals origin date plus horizon",
            "key is JSON [fold, role, origin, product, selling_location, channel, target_date]",
            "eligible equals not reasons; missing history stays unavailable, never fabricated zero",
            "holdout selection_cutoff must precede origin; validation is calibration diagnostic",
            "actual and label_available_at must be null for label-free prediction",
            "median must lie in its interval; mean need not lie between the two quantiles",
            "the predictor assumes verified inputs; JSON schema alone is not PIT qualification",
        ],
        "calibration_windows_and_cutoffs": cutoffs,
        "deployable_service_contract": False,
    }
    card = {
        "version": "forecast-functional-v12-model-card-1.0.0",
        "task": "daily_observed_sales_forecast",
        "target_type": "observed_sales_units",
        "grain": [
            "cohort_id",
            "product_id",
            "selling_location_id",
            "channel",
            "forecast_origin",
            "target_date",
        ],
        "horizon_days": list(range(1, 15)),
        "reporting_windows_days": [7, 14],
        "campaign_id": manifest["campaign_id"],
        "freeze_id": freeze["freeze_id"],
        "forecast_model_status": status,
        "model_family": "cohort_local_empirical_reference_with_frozen_pooled_mean_recipe",
        "method_policy": plan["method_policy"],
        "quality_policy": plan["quality_policy"],
        "quality_qualification_status": desc["quality_qualification_status"],
        "segment_counts": desc["segment_counts"],
        "failed_reasons": desc["failed_reasons"],
        "metrics": {**desc["files"]["metrics.json"], "path": "campaign/metrics.json"},
        "fitted_recipes_sha256": desc["fitted_recipes_sha256"],
        "recipes": recipe_index,
        "cohort_lineage": {
            record["descriptor"]["cohort_id"]: {
                "seed": record["seed"],
                "checkpoint_id": record["checkpoint_id"],
                "cohort_artifact_id": record["cohort_artifact_id"],
                **{
                    key: record["descriptor"][key]
                    for key in ("source_dataset_id", "snapshot_id", "compact_parent")
                },
            }
            for record in desc["cohorts"]
        },
        "split_policy": plan["split_policy"],
        "origin_window": plan["origin_window"],
        "source_configuration": plan["source_configuration"],
        "ai_code_commit": plan["remote_preparation"]["ai_commit"],
        "source_code_commit": plan["remote_preparation"]["source_commit"],
        "code_and_environment": desc["code"],
        "input_signature": "signature.json",
        "input_example": "input_example.json",
        "feature_boundary": {
            "input_fields": list(schema["properties"]["row"]["properties"]),
            "upstream_allowlist": "exact feature_policy and hashes in each retained compact manifest",
            "inventory_features": "excluded",
            "simulation_truth_features": "excluded",
            "observed_labels_used_by_predictor": False,
        },
        "resources": {
            "frozen_budget": plan["resource_plan"],
            "training_duration": None,
            "training_peak_memory": None,
            "cold_load_duration": None,
            "inference_latency": None,
            "measurement_status": "not_captured_by_this_export; do_not_infer_from_timestamps",
        },
        "times": {
            "campaign_evaluated_at": manifest["evaluated_at"],
            "training_run_id": None,
            "training_started_at": None,
            "training_completed_at": None,
        },
        "limitations": [
            "synthetic development qualification is not commercial or production performance evidence",
            "observed sales may be stock constrained; this is not unconstrained demand",
            "validation metrics describe calibration, not an independent test",
            "folds and horizons share targets; row count is not independent sample count",
            "all historic failed and blocked campaigns remain unchanged",
            "portfolio final test has not been opened",
            "AI05 needs a v12 importer and dual-target serving adapter before separate promotion",
        ],
        "deployment": "not_promoted",
        "mlflow_training_claim": False,
    }
    return {"model_card.json": card, "signature.json": signature, "input_example.json": example}


@dataclass(frozen=True)
class _RecordedStats:
    values: dict[str, Any]

    def result(self, _rows: int, _actual: int) -> dict[str, Any]:
        return self.values


@dataclass(frozen=True)
class _RecordedSegment:
    eligible: int
    total: int
    actual: int
    candidate: _RecordedStats
    baseline: _RecordedStats


def _component_types(result: dict[str, Any], rows: int, actual: int) -> None:
    if set(result) != {"rows", "actual_sum", "median", "mean", "interval"}:
        raise SnapshotError("v12_run_metric_component_shape")
    point = {"mae", "mse", "bias_units", "normalized_bias", "wape", "zero_actual_excess_units"}
    expected_fields = {
        "median": point,
        "mean": point,
        "interval": {"mean_width", "width_to_mean_actual", "mean_score", "coverage"},
    }
    for name, fields in expected_fields.items():
        component = result[name]
        complete = component["complete"]
        if type(complete) is not bool or set(component) != {"complete", *fields}:
            raise SnapshotError("v12_run_metric_component_shape")
        for field in fields:
            value = component[field]
            absent = (
                not complete
                or not actual
                and field in {"normalized_bias", "wape", "width_to_mean_actual"}
            )
            if absent:
                if value is not None:
                    raise SnapshotError("v12_run_metric_component_completeness")
            elif (
                not rows
                or type(value) not in (int, float)
                or not math.isfinite(value)
                or field not in {"bias_units", "normalized_bias"}
                and value < 0
                or field == "coverage"
                and value > 1
            ):
                raise SnapshotError("v12_run_metric_component_value")


def validate_metrics(metrics: dict[str, Any], freeze: dict[str, Any], cohorts: list[str]) -> str:
    """Reapply pinned quality2 gates to saved statistics; replay proves their raw inputs."""
    plan = freeze["descriptor"]
    policy = QualityPolicyV2()
    folds = [fold["name"] for fold in plan["split_policy"]["folds"]] + ["pooled"]
    dimensions = {
        "global": ["all"],
        "horizon": [str(value) for value in range(1, 15)],
        **plan["required_dimensions"],
    }
    if (
        plan["quality_policy"] != policy.model_dump(mode="json")
        or metrics["required_segment_inventory"] != dimensions
        or metrics["required_folds"] != folds
        or metrics["required_roles"] != ROLES
        or metrics["cohorts"] != cohorts
        or metrics["portfolio_final_test"] != "not_included_not_opened"
        or metrics["legacy_results_reclassified"] is not False
        or type(metrics["input_complete"]) is not bool
        or metrics["input_complete"] != (metrics["execution_failure"] is None)
    ):
        raise SnapshotError("v12_run_metric_inventory_or_policy")
    required = {
        (fold, role, dimension, value)
        for fold in folds
        for role in ROLES
        for dimension, values in dimensions.items()
        for value in values
    }
    seen = set()
    for segment in metrics["segments"]:
        key = tuple(segment[name] for name in ("fold", "role", "dimension", "value"))
        if key not in required or key in seen:
            raise SnapshotError("v12_run_metric_missing_duplicate_or_extra_segment")
        seen.add(key)
        n, total = segment["eligible_rows"], segment["total_rows"]
        retained = segment["retained_median_baseline"]
        if (
            type(n) is not int
            or type(total) is not int
            or not 0 <= n <= total
            or type(retained) is not bool
        ):
            raise SnapshotError("v12_run_metric_invalid_counts")
        candidate, baseline = segment["candidate"], segment["baseline"]
        actual = candidate["actual_sum"]
        if type(actual) is not int or actual < 0:
            raise SnapshotError("v12_run_metric_invalid_actual_total")
        for result in (candidate, baseline):
            if (
                result["rows"] != n
                or result["actual_sum"] != actual
                or any(
                    type(result[component]["complete"]) is not bool
                    for component in ("median", "mean", "interval")
                )
            ):
                raise SnapshotError("v12_run_metric_component_binding")
            _component_types(result, n, actual)
        recorded = _RecordedSegment(
            n, total, actual, _RecordedStats(candidate), _RecordedStats(baseline)
        )
        # The adapter returns the original metric values without reversing averages
        # into sums, so threshold comparisons retain their original binary64 values.
        expected = _assess(
            cast(_Segment, recorded), cast(Dimension, segment["dimension"]), retained, policy
        )
        calibration = segment["calibration_evidence"]
        if set(calibration) != {"candidate", "baseline"}:
            raise SnapshotError("v12_run_metric_calibration_inventory")
        for side in ("candidate", "baseline"):
            evidence = calibration[side]
            count, missing = evidence["minimum_rows"], evidence["missing_evidence_rows"]
            if (
                set(evidence) != {"minimum_rows", "missing_evidence_rows"}
                or type(missing) is not int
                or missing < 0
                or count is not None
                and (type(count) is not int or count < 0)
                or n > 0
                and segment[side]["interval"]["complete"]
                and count is None
                and missing == 0
            ):
                raise SnapshotError("v12_run_metric_calibration_counts")
            if missing or count is not None and count < policy.minimum_calibration_rows:
                expected["not_ready_reasons"].append(
                    side + "_interval_calibration_evidence_below_minimum"
                )
                expected["status"] = "not_ready"
        expected.update(
            fold=segment["fold"],
            role=segment["role"],
            value=segment["value"],
            retained_median_baseline=retained,
            calibration_evidence=calibration,
        )
        if segment != expected:
            raise SnapshotError("v12_run_metric_quality_gate_contract")
    counts = dict(Counter(segment["status"] for segment in metrics["segments"]))
    failures = dict(
        Counter(reason for segment in metrics["segments"] for reason in segment["failed_reasons"])
    )
    status = (
        "passed"
        if metrics["input_complete"] and all(s["status"] == "passed" for s in metrics["segments"])
        else "not_ready"
    )
    if (
        seen != required
        or metrics["segment_counts"] != counts
        or metrics["failed_reasons"] != failures
        or metrics["status"] != status
    ):
        raise SnapshotError("v12_run_metric_overall_contract")
    indexed = {(s["fold"], s["role"], s["dimension"], s["value"]): s for s in metrics["segments"]}
    processed = sum(
        indexed[(f, r, "global", "all")]["total_rows"] for f in folds[:-1] for r in ROLES
    )
    if type(metrics["processed_rows"]) is not int or metrics["processed_rows"] != processed:
        raise SnapshotError("v12_run_metric_processed_rows_binding")
    # Every dimension partitions the same rows; pooled reports contain every fold.
    for fold in folds:
        for role in ROLES:
            global_result = indexed[(fold, role, "global", "all")]
            for field in ("total_rows", "eligible_rows"):
                if any(
                    sum(indexed[(fold, role, dimension, value)][field] for value in values)
                    != global_result[field]
                    for dimension, values in dimensions.items()
                ):
                    raise SnapshotError("v12_run_metric_dimension_counts_binding")
                if fold == "pooled" and global_result[field] != sum(
                    indexed[(name, role, "global", "all")][field] for name in folds[:-1]
                ):
                    raise SnapshotError("v12_run_metric_pooled_counts_binding")
    return status


def _paths(root: Path) -> list[str]:
    checked_directory(root)
    names = []
    for base, directories, files in os.walk(root, followlinks=False):
        for name in (*directories, *files):
            path = Path(base) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise SnapshotError("v12_run_symlink_or_special_file")
            if stat.S_ISREG(mode):
                names.append(path.relative_to(root).as_posix())
                if len(names) > MAX_FILES:
                    raise SnapshotError("v12_run_file_budget")
    return sorted(names)


def _receipts(roots: dict[str, Path]) -> dict[str, dict[str, Any]]:
    result = {}
    total = 0
    for namespace, parent in sorted(roots.items()):
        for name in _paths(parent):
            size, digest = file_hash(parent, name)
            total += size
            result[namespace + "/" + name] = {"size_bytes": size, "sha256": digest}
            if len(result) > MAX_FILES or total > MAX_BYTES:
                raise SnapshotError("v12_run_payload_budget")
    return result


def _validate_complete_results(
    campaign: Path, desc: dict[str, Any], metrics: dict[str, Any]
) -> None:
    """Bind saved exposure and row inventories to every immutable prepared cohort."""
    freeze = desc["freeze"]
    plan = freeze["descriptor"]
    records = desc["cohorts"]
    folds = [fold["name"] for fold in plan["split_policy"]["folds"]]
    results = read_json(campaign, "cohort_results.json")
    ids = [record["descriptor"]["cohort_id"] for record in records]
    if set(results) != set(ids):
        raise SnapshotError("v12_run_result_cohort_inventory")
    for key, excluded in (
        ("source_dataset_id", "previously_used_source_ids"),
        ("snapshot_id", "previously_used_snapshot_ids"),
    ):
        values = [record["descriptor"][key] for record in records]
        if len(set(values)) != len(records) or set(values) & set(plan[excluded]):
            raise SnapshotError("v12_run_nonindependent_or_previously_used_cohort")
    totals: Counter[tuple[str, str, str]] = Counter()
    exposures = []
    for record in records:
        parent = record["descriptor"]
        cohort_id = parent["cohort_id"]
        result = results[cohort_id]
        if (
            cohort_id != f"seed-{record['seed']}"
            or record["cohort_artifact_id"] != "forecast-cohort-sha256-" + canonical_sha256(parent)
            or any(
                parent[key] != plan[key]
                for key in ("split_policy", "origin_window", "method_policy")
            )
            or set(parent["recipes"]) != set(folds)
            or set(result) != {"exposure", "counts", "logical_predictions_sha256"}
            or not re.fullmatch(r"[0-9a-f]{64}", result["logical_predictions_sha256"])
        ):
            raise SnapshotError("v12_run_prepared_cohort_binding")
        expected_counts = {}
        for fold in folds:
            for role in ROLES:
                counts = {
                    name: parent["complete_compact_counts"].get(f"{fold}:{role}:{name}", 0)
                    for name in ("total", "eligible")
                }
                if any(type(value) is not int for value in counts.values()) or not (
                    0 <= counts["eligible"] <= counts["total"]
                ):
                    raise SnapshotError("v12_run_prepared_cohort_counts")
                expected_counts[f"{fold}:{role}"] = counts
                for name, count in counts.items():
                    totals[(fold, role, name)] += count
                    totals[("pooled", role, name)] += count
        if result["counts"] != expected_counts:
            raise SnapshotError("v12_run_cohort_result_counts_binding")
        receipt = result["exposure"]
        body = receipt["descriptor"]
        binding = {
            "freeze_id": freeze["freeze_id"],
            "seed": record["seed"],
            "source_dataset_id": parent["source_dataset_id"],
            "snapshot_id": parent["snapshot_id"],
            "fitted_recipes_sha256": desc["fitted_recipes_sha256"],
            "status": "holdout_opened_cannot_become_unseen_again",
        }
        if (
            set(receipt) != {"exposure_id", "descriptor"}
            or set(body) != {*binding, "opened_at"}
            or any(body[key] != value for key, value in binding.items())
            or receipt["exposure_id"] != "cohort-exposure-sha256-" + canonical_sha256(body)
        ):
            raise SnapshotError("v12_run_exposure_binding")
        try:
            opened = datetime.fromisoformat(body["opened_at"])
            if opened.tzinfo is None or opened.utcoffset() is None:
                raise ValueError("timezone required")
        except (TypeError, ValueError) as exc:
            raise SnapshotError("v12_run_exposure_time") from exc
        exposures.append(receipt["exposure_id"])
        expected_marker = {
            "cohort_id": cohort_id,
            "fitted_recipes_sha256": desc["fitted_recipes_sha256"],
            "prediction": desc["files"][f"predictions/{cohort_id}.jsonl.gz"],
            "result": result,
        }
        if read_json(campaign, f"completed-cohorts/{cohort_id}.json") != expected_marker:
            raise SnapshotError("v12_run_completed_cohort_binding")
    if desc["exposure_ids"] != sorted(exposures) or len(set(exposures)) != len(records):
        raise SnapshotError("v12_run_exposure_complete_inventory")
    for segment in metrics["segments"]:
        if segment["dimension"] == "global" and any(
            segment[name + "_rows"] != totals[(segment["fold"], segment["role"], name)]
            for name in ("total", "eligible")
        ):
            raise SnapshotError("v12_run_metric_prepared_counts_binding")
    if metrics["processed_rows"] != sum(
        totals[(fold, role, "total")] for fold in folds for role in ROLES
    ):
        raise SnapshotError("v12_run_processed_prepared_counts_binding")
    preparation = {
        "freeze_id": freeze["freeze_id"],
        "cohorts": records,
        "fitted_recipes_sha256": desc["fitted_recipes_sha256"],
        "all_final_recipes_saved_before_holdout_open": True,
    }
    if read_json(campaign, "preparation_manifest.json") != preparation:
        raise SnapshotError("v12_run_preparation_manifest_binding")


def _validate_parents(
    campaign: Path, replay: Path, checkpoints: Sequence[Path]
) -> tuple[dict[str, Any], dict[str, Any], dict[int, Path], str]:
    manifest = load_campaign(campaign)
    desc = manifest["descriptor"]
    freeze = desc["freeze"]
    records = desc["cohorts"]
    seeds = freeze["descriptor"]["seeds"]
    if (
        not 1 <= len(seeds) <= 64
        or [record["seed"] for record in records] != seeds
        or len(checkpoints) != len(seeds)
        or desc["all_preregistered_cohorts_included"] is not True
        or desc["all_final_recipes_saved_before_holdout_open"] is not True
        or desc["quality_thresholds_changed"] is not False
        or desc["training_rows_read_during_scoring"] != 0
        or desc["refit_calls"] != 0
        or read_json(campaign, "freeze.json") != freeze
    ):
        raise SnapshotError("v12_run_campaign_scope_or_complete_inventory")
    ids = [record["descriptor"]["cohort_id"] for record in records]
    if len(set(ids)) != len(ids):
        raise SnapshotError("v12_run_duplicate_cohort")
    metrics = read_json(campaign, "metrics.json")
    quality = validate_metrics(metrics, freeze, ids)
    _validate_complete_results(campaign, desc, metrics)
    if (
        quality != desc["quality_qualification_status"]
        or desc["segment_counts"] != metrics["segment_counts"]
        or desc["failed_reasons"] != metrics["failed_reasons"]
    ):
        raise SnapshotError("v12_run_campaign_quality_binding")
    proof = read_json(replay, "replay_receipt.json")
    replay_desc = proof["descriptor"]
    predictions = {
        cohort: {
            "status": "passed",
            **{
                key: desc["files"][f"predictions/{cohort}.jsonl.gz"][key]
                for key in ("size_bytes", "sha256")
            },
        }
        for cohort in ids
    }
    model_status = "ready" if quality == "passed" else "not_ready"
    expected = {
        "status": "passed",
        "scope": "independent_replay_of_same_frozen_exposed_campaign",
        "campaign_id": manifest["campaign_id"],
        "freeze_id": freeze["freeze_id"],
        "fitted_recipes_sha256": desc["fitted_recipes_sha256"],
        "prediction_files": predictions,
        "metrics_sha256": file_hash(campaign, "metrics.json")[1],
        "all_preregistered_cohorts_included": True,
        "refit_calls": 0,
        "new_qualification": False,
        "forecast_model_status": model_status,
        "code": campaign_code(),
    }
    if replay_desc != expected or proof["replay_id"] != (
        "functional-v12-replay-sha256-" + canonical_sha256(replay_desc)
    ):
        raise SnapshotError("v12_run_independent_replay_binding")
    replay_names = {
        name
        for name in desc["files"]
        if name
        in {
            "metrics.json",
            "cohort_results.json",
            "fitted_recipes.json",
            "freeze.json",
            "preparation_manifest.json",
        }
        or name.startswith(("recipes/", "calibration/"))
    }
    inventory(replay, {*replay_names, "replay_receipt.json"})
    for name in replay_names:
        if file_hash(replay, name) != file_hash(campaign, name):
            raise SnapshotError("v12_run_replay_saved_parameters_or_metrics_differ")
    paths = {}
    indexed = {record["seed"]: record for record in records}
    for checkpoint in checkpoints:
        verified = verify_checkpoint(checkpoint)
        lineage = verified["descriptor"]["lineage"]
        seed = lineage["seed"]
        if seed not in indexed or seed in paths:
            raise SnapshotError("v12_run_duplicate_or_unplanned_checkpoint")
        record = indexed[seed]
        parent = record["descriptor"]
        manifest_size, manifest_hash = file_hash(checkpoint, "checkpoint_manifest.json")
        if (
            verified["checkpoint_id"] != record["checkpoint_id"]
            or verified["archive"] != record["checkpoint_archive"]
            or record["checkpoint_manifest"]
            != {
                "path": "checkpoint_manifest.json",
                "size_bytes": manifest_size,
                "sha256": manifest_hash,
            }
            or record["checkpoint_total_bytes"] != verified["archive"]["size_bytes"] + manifest_size
            or lineage["freeze_id"] != freeze["freeze_id"]
            or lineage["cohort_id"] != parent["cohort_id"]
            or lineage["prepared_cohort_id"] != record["cohort_artifact_id"]
            or any(lineage[key] != parent[key] for key in ("source_dataset_id", "snapshot_id"))
        ):
            raise SnapshotError("v12_run_checkpoint_parent_binding")
        paths[seed] = checkpoint
    if set(paths) != set(seeds):
        raise SnapshotError("v12_run_missing_checkpoint")
    return manifest, proof, paths, model_status


def _clone(parent: Path, name: str, target: Path, receipt: dict[str, Any], reserve: int) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    expected = receipt["size_bytes"], receipt["sha256"]
    if file_hash(parent, name) != expected:
        raise SnapshotError("v12_run_input_changed_before_clone")
    with regular_file(parent, name) as source:
        original = os.fstat(source.fileno())
        try:
            os.link(parent / name, target, follow_symlinks=False)
            mode = "hardlink"
            linked = target.lstat()
            if not stat.S_ISREG(linked.st_mode) or (linked.st_dev, linked.st_ino) != (
                original.st_dev,
                original.st_ino,
            ):
                raise SnapshotError("v12_run_hardlink_identity")
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            if shutil.disk_usage(target.parent).free < receipt["size_bytes"] + reserve:
                raise SnapshotError("v12_run_cross_device_copy_space_budget") from exc
            with target.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024**2)
            mode = "copy_after_cross_device_preflight"
    if file_hash(parent, name) != expected or file_hash(target.parent, target.name) != expected:
        raise SnapshotError("v12_run_input_or_clone_changed")
    return mode


def export_run(
    campaign: Path,
    replay: Path,
    checkpoints: Sequence[Path],
    output: Path,
    code_commit: str,
    *,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> Path:
    """Publish an entire qualified or failed campaign and its retained inputs atomically."""
    absolute_output = output.absolute()
    if ".." in absolute_output.parts or any(
        path.is_symlink() for path in (absolute_output, *absolute_output.parents)
    ):
        raise SnapshotError("v12_run_unsafe_output_path")
    if not re.fullmatch(r"[0-9a-f]{40}", code_commit) or (
        type(min_free_bytes) is not int or min_free_bytes < MIN_FREE_BYTES
    ):
        raise SnapshotError("v12_run_commit_or_space_reserve")
    manifest, proof, paths, status = _validate_parents(campaign, replay, checkpoints)
    freeze = manifest["descriptor"]["freeze"]
    if code_commit != freeze["descriptor"]["remote_preparation"]["ai_commit"]:
        raise SnapshotError("v12_run_frozen_ai_commit_binding")
    parents = {"campaign": campaign, "replay": replay} | {
        f"checkpoints/seed-{seed}": parent for seed, parent in paths.items()
    }
    for parent in parents.values():
        if output.absolute().is_relative_to(parent.absolute()) or parent.absolute().is_relative_to(
            output.absolute()
        ):
            raise SnapshotError("v12_run_output_overlaps_input")
    receipts = _receipts(parents)
    output.mkdir(parents=True, exist_ok=True)
    checked_directory(output)
    output_device = output.stat().st_dev
    copied_bytes = sum(
        receipt["size_bytes"]
        for name, receipt in receipts.items()
        if _parent_file(parents, name)[0].stat().st_dev != output_device
    )
    metadata_reserve = 4 * 1024**2
    if shutil.disk_usage(output).free < copied_bytes + min_free_bytes + metadata_reserve:
        raise SnapshotError("v12_run_publication_space_preflight")
    with tempfile.TemporaryDirectory(prefix=".functional-v12-run-", dir=output) as temporary:
        root = Path(temporary)
        transport: Counter[str] = Counter()
        for name, receipt in receipts.items():
            parent, relative = _parent_file(parents, name)
            transport[_clone(parent, relative, root / name, receipt, min_free_bytes)] += 1
        if _receipts(parents) != receipts:
            raise SnapshotError("v12_run_inputs_changed_during_export")
        handoff = _handoff(manifest, proof, status)
        reports = _model_reports(root / "campaign", manifest, status)
        reports.update({"handoff.json": handoff, "storage_receipt.json": dict(transport)})
        for name, body in reports.items():
            write_private(root / name, canonical_bytes(body) + b"\n")
            size, digest = file_hash(root, name)
            receipts[name] = {"size_bytes": size, "sha256": digest}
        descriptor = {
            "version": VERSION,
            "campaign_id": manifest["campaign_id"],
            "replay_id": proof["replay_id"],
            "freeze_id": freeze["freeze_id"],
            "forecast_model_status": status,
            "quality_qualification_status": handoff["quality_qualification_status"],
            "ai_code_commit": code_commit,
            "code": campaign_code(),
            "checkpoints": {str(seed): f"checkpoints/seed-{seed}" for seed in sorted(paths)},
            "files": receipts,
            "bytes": sum(ref["size_bytes"] for ref in receipts.values()),
        }
        result = {
            "run_id": "functional-v12-run-sha256-" + canonical_sha256(descriptor),
            "descriptor": descriptor,
            "exported_at": datetime.now(UTC).isoformat(),
        }
        write_private(root / "run_manifest.json", canonical_bytes(result) + b"\n")
        verify_run(root)
        fsync_tree(root)
        destination = output / str(result["run_id"])
        publish_noreplace(root, destination)
    return destination


def _parent_file(parents: dict[str, Path], name: str) -> tuple[Path, str]:
    for namespace in sorted(parents, key=len, reverse=True):
        if name.startswith(namespace + "/"):
            return parents[namespace], name[len(namespace) + 1 :]
    raise SnapshotError("v12_run_unbound_file_namespace")


def verify_run(root: Path) -> dict[str, Any]:
    """Verify every retained byte, source checkpoint, code pin and qualification binding."""
    manifest = read_json(root, "run_manifest.json")
    desc = manifest["descriptor"]
    if (
        manifest["run_id"] != "functional-v12-run-sha256-" + canonical_sha256(desc)
        or desc["version"] != VERSION
        or desc["code"] != campaign_code()
        or not 1 <= len(desc["checkpoints"]) <= 64
        or not 1 <= len(desc["files"]) <= MAX_FILES
        or not 0 <= desc["bytes"] <= MAX_BYTES
    ):
        raise SnapshotError("v12_run_identity_code_or_budget")
    inventory(root, {"run_manifest.json", *desc["files"]})
    total = 0
    for name, receipt in desc["files"].items():
        if file_hash(root, name) != (receipt["size_bytes"], receipt["sha256"]):
            raise SnapshotError("v12_run_file_checksum")
        total += receipt["size_bytes"]
    if total != desc["bytes"]:
        raise SnapshotError("v12_run_total_bytes")
    checkpoints = []
    for seed, name in desc["checkpoints"].items():
        if not re.fullmatch(r"[0-9]+", seed) or name != f"checkpoints/seed-{seed}":
            raise SnapshotError("v12_run_checkpoint_path")
        checkpoints.append(root / name)
    campaign, replay, paths, status = _validate_parents(
        root / "campaign", root / "replay", checkpoints
    )
    freeze = campaign["descriptor"]["freeze"]
    handoff = read_json(root, "handoff.json")
    if (
        desc["campaign_id"] != campaign["campaign_id"]
        or desc["replay_id"] != replay["replay_id"]
        or desc["freeze_id"] != freeze["freeze_id"]
        or desc["forecast_model_status"] != status
        or desc["quality_qualification_status"]
        != campaign["descriptor"]["quality_qualification_status"]
        or desc["ai_code_commit"] != freeze["descriptor"]["remote_preparation"]["ai_commit"]
        or set(desc["checkpoints"]) != {str(seed) for seed in paths}
        or any(
            handoff[key] != desc[key]
            for key in (
                "campaign_id",
                "replay_id",
                "freeze_id",
                "forecast_model_status",
                "quality_qualification_status",
            )
        )
        or handoff["independent_replay"] != "passed"
        or handoff["all_preregistered_cohorts_included"] is not True
        or handoff["source_regeneration"] is not False
        or handoff["model_refits"] != 0
    ):
        raise SnapshotError("v12_run_qualification_binding")
    if handoff != _handoff(campaign, replay, status):
        raise SnapshotError("v12_run_handoff_contract")
    for name, expected in _model_reports(root / "campaign", campaign, status).items():
        if name not in desc["files"] or read_json(root, name) != expected:
            raise SnapshotError("v12_run_model_report_binding")
    return manifest


load_run = verify_run
