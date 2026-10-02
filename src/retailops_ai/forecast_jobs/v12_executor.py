"""Trusted AI05 script, executed with -I -B by the operator's installed AI04 wheel.

This file intentionally imports AI04 v12 modules only inside the child entry point.
It never imports AI05 runtime code into the pinned AI04 environment.
"""

import hashlib
import resource
import sys
import time
from copy import deepcopy
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any, cast

MAX_REQUEST_BYTES = 4 * 1024**2
MAX_ROWS = 256


def peak_rss_bytes() -> int:
    """Measure this executable's memory, excluding pre-exec parent usage on Linux."""
    if sys.platform == "linux":
        with Path("/proc/self/status").open("rb") as stream:
            raw = stream.read(65537)
        values = [line.split() for line in raw.splitlines() if line.startswith(b"VmHWM:")]
        if (
            len(raw) > 65536
            or len(values) != 1
            or len(values[0]) != 3
            or values[0][2] != b"kB"
            or not values[0][1].isdigit()
            or int(values[0][1]) <= 0
        ):
            raise ValueError("runtime_memory_measurement_unavailable")
        return int(values[0][1]) * 1024
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak * (1 if sys.platform == "darwin" else 1024))


ACCEPTED_RUN = (
    "functional-v12-run-sha256-345a725d435a477374292cb9483350fb5c50c8ba87d06668c727e0a9f964fb6b"
)
ACCEPTED_MANIFEST = "29bf6837ca2cb7504213168743239b037041f375763bc8f01ae28c1f6d09b26e"
ACCEPTED_DECISION = "11cd0e1fdd10629543bcb66aaedb3bd9b7b7fc5b31473575b998d951c93f00d2"
ACCEPTED_DECISION_BYTES = 3513


def development_acceptance_matches(
    acceptance: dict[str, Any] | None,
    run_id: str = ACCEPTED_RUN,
    manifest_sha256: str = ACCEPTED_MANIFEST,
) -> bool:
    """Shared parent/isolated-child check; never a quality or production override."""
    return (
        acceptance
        == {
            "version": "v12-development-acceptance-1.0.0",
            "scope": "local_development_only",
            "decision": {"sha256": ACCEPTED_DECISION, "size_bytes": ACCEPTED_DECISION_BYTES},
            "production_deployment_authorized": False,
            "original_quality_reclassified": False,
        }
        and acceptance["production_deployment_authorized"] is False
        and acceptance["original_quality_reclassified"] is False
        and type(acceptance["decision"]["size_bytes"]) is int
        and run_id == ACCEPTED_RUN
        and manifest_sha256 == ACCEPTED_MANIFEST
    )


def inference_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Change only the role; retain the frozen category/channel/grain/null-label constraints."""
    result = deepcopy(schema)
    row = result["properties"]["row"]
    fields = row["properties"]
    if (
        fields["role"] != {"enum": ["validation", "development_holdout"]}
        or fields["actual"] != {"type": "null"}
        or fields["label_available_at"] != {"type": "null"}
        or not {"role", "actual", "label_available_at"} <= set(row["required"])
    ):
        raise ValueError("v12_inference_original_signature_boundary")
    fields["role"] = {"enum": ["inference"]}
    return result


def calendar_exclusion(row: dict[str, Any]) -> str | None:
    """A confirmed closure is retained; unknown or contradictory calendars fail closed."""
    values = [v for v in row["values"] if v["name"] == "target_location_open"]
    if len(values) != 1:
        raise ValueError("v12_runtime_calendar_unavailable")
    value = values[0]
    if value["kind"] != "calendar" or value["status"] != "available":
        raise ValueError("v12_runtime_calendar_unavailable")
    if row["target_calendar_eligible"] is True and value["value"] is True:
        return None
    if row["target_calendar_eligible"] is False and value["value"] is False:
        return "closed_target"
    raise ValueError("v12_runtime_calendar_contradiction")


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
    inference = request.get("inference")
    role = "inference" if inference is not None else "development_holdout"
    if (
        origin <= fold.selection_cutoff
        or origin > datetime.now(UTC)
        or (
            inference is None
            and not fold.development_holdout.start <= origin.date() <= fold.development_holdout.end
        )
        or any(row.forecast_origin != origin for row in rows)
    ):
        raise ValueError("v12_runtime_input_origin")
    schema = signature["input_schema"]
    if (
        signature["input_schema_sha256"] != canonical_sha256(schema)
        or signature["deployable_service_contract"] is not False
    ):
        raise ValueError("v12_runtime_input_signature")
    if inference is not None:
        source = inference["source_policy"]
        approved = inference["purpose"] == "qualified_forecast_v12"
        if (
            not (
                (
                    descriptor["forecast_model_status"] == "ready"
                    and descriptor["quality_qualification_status"] == "passed"
                    and inference.get("development_acceptance") is None
                )
                or (
                    descriptor["forecast_model_status"] == "not_ready"
                    and descriptor["quality_qualification_status"] == "not_ready"
                    and development_acceptance_matches(
                        inference.get("development_acceptance"),
                        pin["run_id"],
                        pin["manifest"]["sha256"],
                    )
                )
            )
            or inference["version"] != "forecast-v12-inference-context-1.0.0"
            or inference["purpose"]
            not in ("serving_load_predict_acceptance", "qualified_forecast_v12")
            or inference["serving_eligible"] is not approved
            or (inference["release_id"] is not None) != approved
            or (inference["qualification_id"] is not None) != approved
            or source["version"] != "forecast-v12-source-policy-1.0.0"
            or source["mode"] != "same_verified_feature_package"
            or source["input_role"] != "inference"
            or source["source_change"] != "new_qualification_and_review_required"
            or source["feature_set_id"] != feature_manifest.feature_set_id
            or source["curated_descriptor_sha256"] != parent.curated_descriptor_sha256
        ):
            raise ValueError("v12_inference_source_or_approval_binding")
        schema = inference_schema(schema)
        if canonical_sha256(schema) != source["input_schema_sha256"]:
            raise ValueError("v12_inference_schema_binding")
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
        ):
            raise ValueError("v12_runtime_history_or_calendar_not_eligible")
        exclusion = calendar_exclusion(row.model_dump(mode="json"))
        points, bands = empirical_baselines(row, history)
        values = {value.name: value.value for value in row.values}
        key = canonical_bytes(
            [
                fold.name,
                role,
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
            "role": role,
            "origin": origin.isoformat(),
            "volume": volume_bin(cast(float | None, values["rolling_mean_28"]), QualityPolicy()),
            "category": values["category_id"],
            "channel": row.channel,
            "horizon": row.horizon_days,
            "eligible": exclusion is None,
            "reasons": [exclusion] if exclusion else [],
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
        prediction = {
            "key": key,
            "candidate": candidate.model_dump(mode="json"),
            "baseline": baseline.model_dump(mode="json"),
            "metadata": metadata,
        }
        if exclusion is not None:
            prediction["exclusion_reason"] = exclusion
        predictions.append(prediction)
    peak_bytes = peak_rss_bytes()
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
        "published_forecast_outputs": 0,
    }
    if inference is None:
        result["serving_eligible"] = False
    else:
        result["inference"] = inference
    sys.stdout.buffer.write(canonical_bytes(result))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.stderr.write("v12_runtime_child_failed\n")
        raise SystemExit(1) from None
