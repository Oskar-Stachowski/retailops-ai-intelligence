"""Trusted AI05 script, executed with -I -B by the operator's installed AI04 wheel.

This file intentionally imports AI04 v12 modules only inside the child entry point.
It never imports AI05 runtime code into the pinned AI04 environment.
"""

import hashlib
import resource
import sys
import time
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, cast

MAX_REQUEST_BYTES = 4 * 1024**2
MAX_ROWS = 256


def main() -> None:
    started = time.monotonic()
    from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

    import retailops_ai
    from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
    from retailops_ai.forecasting.contract import make_origin
    from retailops_ai.forecasting.features import OriginFeatures
    from retailops_ai.forecasting.features_contract import TABLES, HistoryContext, InputRow

    # Fixed module names from the separately installed AI04 wheel; never export-selected code.
    campaign_code = import_module("retailops_ai.forecasting.functional_v12_campaign").campaign_code
    observation_from_compact = import_module(
        "retailops_ai.forecasting.functional_v12_cohort"
    ).observation_from_compact
    PreparedV12Predictor = import_module(
        "retailops_ai.forecasting.functional_v12_recipe"
    ).PreparedV12Predictor
    empirical_baselines = import_module(
        "retailops_ai.forecasting.functional_recipe"
    ).empirical_baselines
    from retailops_ai.forecasting.manifest_contract import FeatureManifest, FeaturePolicy, FoldPlan
    from retailops_ai.forecasting.quality_contract import QualityPolicy
    from retailops_ai.forecasting.quality_metrics import volume_bin
    from retailops_ai.source_snapshot.files import checked_directory, decode_json, read_bytes

    site = (
        Path(sys.prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    if not Path(retailops_ai.__file__).resolve().is_relative_to(site.resolve()):
        raise ValueError("v12_runtime_installed_wheel_required")
    request = decode_json(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
    root, pin, inputs = checked_directory(Path(request["root"])), request["pin"], request["inputs"]

    def bound(name: str, ref: dict[str, Any]) -> dict[str, Any]:
        raw = read_bytes(root, name)
        if (len(raw), hashlib.sha256(raw).hexdigest()) != (ref["size_bytes"], ref["sha256"]):
            raise ValueError("v12_runtime_artifact_changed")
        return decode_json(raw)

    manifest = bound("run_manifest.json", pin["manifest"])
    descriptor = manifest["descriptor"]
    if (
        manifest["run_id"] != pin["run_id"]
        or manifest["run_id"] != "functional-v12-run-sha256-" + canonical_sha256(descriptor)
        or descriptor["code"] != campaign_code()
        or descriptor["code"]["code_sha256"] != pin["code_sha256"]
        or descriptor["code"]["dependency_lock_sha256"] != pin["dependency_lock_sha256"]
        or any(
            descriptor[key] != pin[key]
            for key in ("campaign_id", "freeze_id", "replay_id", "forecast_model_status")
        )
        or pin["purpose"] != "offline_load_predict_acceptance"
        or pin["serving_eligible"] is not False
    ):
        raise ValueError("v12_runtime_code_or_run_pin")
    signature = bound("signature.json", descriptor["files"]["signature.json"])
    card = bound("model_card.json", descriptor["files"]["model_card.json"])
    freeze = bound("campaign/freeze.json", descriptor["files"]["campaign/freeze.json"])
    reference = card["recipes"][pin["cohort_id"]][pin["fold"]["name"]]["artifact"]
    lineage = card["cohort_lineage"][pin["cohort_id"]]
    if (
        reference["path"] != pin["recipe_path"]
        or reference["recipe_id"] != pin["recipe_id"]
        or descriptor["files"][pin["recipe_path"]] != pin["recipe"]
        or descriptor["files"]["signature.json"] != pin["signature"]
        or any(lineage[key] != pin[key] for key in ("source_dataset_id", "snapshot_id"))
        or pin["fold"] not in freeze["descriptor"]["split_policy"]["folds"]
    ):
        raise ValueError("v12_runtime_recipe_or_lineage_pin")
    recipe = bound(pin["recipe_path"], pin["recipe"])
    fold = FoldPlan.model_validate_json(canonical_bytes(pin["fold"]))
    if (
        recipe["recipe_id"] != pin["recipe_id"]
        or recipe["fold"] != fold.name
        or datetime.fromisoformat(recipe["selection_cutoff"]) != fold.selection_cutoff
        or recipe["policy"] != freeze["descriptor"]["method_policy"]
        or pin["cohort_id"] not in recipe["support"]["cohort_ids"]
        or recipe["policy"]["mean_variant"] == "hgb_blend"
    ):
        raise ValueError("v12_runtime_recipe_binding")
    predictor = PreparedV12Predictor(recipe)
    feature_manifest = FeatureManifest.model_validate_json(
        canonical_bytes(inputs["feature_manifest"])
    )
    parent = feature_manifest.descriptor.parent
    if (
        inputs["profile_id"]
        != "batch-profile-sha256-"
        + canonical_sha256({key: value for key, value in inputs.items() if key != "profile_id"})
        or parent.source_dataset_id != pin["source_dataset_id"]
        or parent.snapshot_id != pin["snapshot_id"]
        or feature_manifest.descriptor.code.dependency_lock_sha256 != pin["dependency_lock_sha256"]
        or feature_manifest.descriptor.resolved_policy != FeaturePolicy()
        or not 1 <= len(inputs["rows"]) <= MAX_ROWS
        or inputs["horizon_days"] not in (7, 14)
    ):
        raise ValueError("v12_runtime_input_identity_or_parent")
    rows = [InputRow.model_validate_json(canonical_bytes(value)) for value in inputs["rows"]]
    histories = [
        HistoryContext.model_validate_json(canonical_bytes(value)) for value in inputs["histories"]
    ]
    by_hash = {history.content_sha256(): history for history in histories}
    if len(by_hash) != len(histories) or set(by_hash) != {
        row.history_context_sha256 for row in rows
    }:
        raise ValueError("v12_runtime_history_inventory")
    scope = inputs["scope"]
    expected = sorted(
        (product, location, scope["channel"], h)
        for product in scope["product_ids"]
        for location in scope["selling_location_ids"]
        for h in range(1, inputs["horizon_days"] + 1)
    )
    if [
        (row.product_id, row.selling_location_id, row.channel, row.horizon_days) for row in rows
    ] != expected:
        raise ValueError("v12_runtime_input_scope")
    origin = datetime.fromisoformat(inputs["as_of_time"])
    if (
        origin <= fold.selection_cutoff
        or origin > datetime.now(UTC)
        or not fold.development_holdout.start <= origin.date() <= fold.development_holdout.end
        or any(row.forecast_origin != origin for row in rows)
    ):
        raise ValueError("v12_runtime_input_origin")
    schema = signature["input_schema"]
    if (
        signature["input_schema_sha256"] != canonical_sha256(schema)
        or signature["deployable_service_contract"] is not False
    ):
        raise ValueError("v12_runtime_input_signature")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    policy = FeaturePolicy()
    historical = {}
    for digest, history in by_hash.items():
        view = OriginFeatures({table: [] for table in TABLES}, make_origin(origin.date()))
        historical[digest] = view.historical_values(history)
    cold_seconds = time.monotonic() - started
    compute_started = time.monotonic()
    predictions = []
    for row in rows:
        history = by_hash[row.history_context_sha256]
        known = [point.business_date for point in history.points if point.status != "missing"]
        if (
            (
                history.product_id,
                history.selling_location_id,
                history.channel,
                history.forecast_origin,
            )
            != (row.product_id, row.selling_location_id, row.channel, origin)
            or row.history_active_days != len(history.points)
            or row.history_known_days != len(known)
            or row.history_closed_days != sum(point.status == "closed" for point in history.points)
            or {value.name: value for value in row.values if value.kind == "observed"}
            != historical[row.history_context_sha256]
            or row.history_active_days < policy.minimum_active_history_days
            or len(known) < policy.minimum_known_history_days
            or not known
            or (origin.date() - max(known)).days > policy.maximum_observation_age_days
            or not row.target_calendar_eligible
        ):
            raise ValueError("v12_runtime_history_or_calendar_not_eligible")
        points, bands = empirical_baselines(row, history)
        values = {value.name: value.value for value in row.values}
        key = canonical_bytes(
            [
                fold.name,
                "development_holdout",
                origin.isoformat(),
                row.product_id,
                row.selling_location_id,
                row.channel,
                row.target_date.isoformat(),
            ]
        ).decode()
        compact = {
            "key": key,
            "fold": fold.name,
            "role": "development_holdout",
            "origin": origin.isoformat(),
            "volume": volume_bin(cast(float | None, values["rolling_mean_28"]), QualityPolicy()),
            "category": values["category_id"],
            "channel": row.channel,
            "horizon": row.horizon_days,
            "eligible": True,
            "reasons": [],
            "actual": None,
            "label_available_at": None,
            "baseline_points": points,
            "baseline_bands": {
                name: list(band) if band is not None else None for name, band in bands.items()
            },
        }
        validator.validate(
            {"cohort_id": pin["cohort_id"], "recipe_id": pin["recipe_id"], "row": compact}
        )
        candidate, baseline, metadata = predictor.predict(
            observation_from_compact(compact, pin["cohort_id"])
        )
        predictions.append(
            {
                "key": key,
                "candidate": candidate.model_dump(mode="json"),
                "baseline": baseline.model_dump(mode="json"),
                "metadata": metadata,
            }
        )
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = int(peak if sys.platform == "darwin" else peak * 1024)
    result = {
        "pin": pin,
        "profile_id": inputs["profile_id"],
        "predictions": predictions,
        "predictions_sha256": canonical_sha256(predictions),
        "cold_load_seconds": cold_seconds,
        "compute_seconds": time.monotonic() - compute_started,
        "peak_rss_bytes": peak_bytes,
        "generated_at": datetime.now(UTC).isoformat(),
        "model_refits": 0,
        "source_generation": False,
        "serving_eligible": False,
        "published_forecast_outputs": 0,
    }
    sys.stdout.buffer.write(canonical_bytes(result))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.stderr.write("v12_runtime_child_failed\n")
        raise SystemExit(1) from None
