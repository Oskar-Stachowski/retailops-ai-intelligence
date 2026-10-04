"""Pinned saved-model scoring, complete deterministic output, no private truth access."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from retailops_ai.anomaly_detectors.protocol import Scope, Window, series_key
from retailops_ai.anomaly_portfolio.inputs import VerifiedFeatures
from retailops_ai.anomaly_portfolio.lifecycle_contract import Release
from retailops_ai.anomaly_portfolio.model import load, score
from retailops_ai.anomaly_portfolio.serving_contract import Item
from retailops_ai.source_snapshot.files import canonical_json, json_sha256


def batch(
    release: Release,
    model_path: Path,
    frame: VerifiedFeatures,
    scopes: tuple[Scope, ...],
    window: Window,
    as_of: datetime,
    generated_at: datetime,
) -> tuple[dict[str, Any], list[Item]]:
    release = Release.model_validate_json(release.model_dump_json())
    qualification = release.binding.qualification
    if any(g.status != "passed" for g in qualification.gates.values()):
        raise ValueError("anomaly_batch_unqualified_release")
    model = load(model_path, qualification.model.sha256)
    if (
        model.descriptor.source_dataset_id != qualification.source_dataset_id
        or model.descriptor.qualified_anomaly_input_id != qualification.qualified_anomaly_input_id
        or model.descriptor.dependency_lock_sha256 != qualification.dependency_lock_sha256
        or as_of.utcoffset() != timedelta(0)
        or generated_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("anomaly_batch_model_or_clock_binding")
    points = list(frame.points())
    indexed_points = {(*series_key(p), p.business_date): p for p in points}
    decisions = score(model, points, scopes, window, qualification.model_family, "batch", as_of)
    desc = frame.manifest.descriptor
    metadata = {
        "version": "anomaly-complete-batch-1.0.0",
        "release_id": release.release_id,
        "model_version": release.binding.model_version,
        "model_sha256": qualification.model.sha256,
        "source_dataset_id": desc.coverage.descriptor.source_dataset_id,
        "feature_id": frame.manifest.qualified_anomaly_input_id,
        "feature_manifest_sha256": frame.manifest_sha256,
        "window": window.model_dump(mode="json"),
        "as_of": as_of.isoformat(),
        "scopes": [s.model_dump(mode="json") for s in sorted(scopes, key=series_key)],
        "decisions_sha256": json_sha256([d.model_dump(mode="json") for d in decisions]),
        "row_count": len(decisions),
        "truth_access": "excluded",
        "transport_durability": "offline_only",
    }
    batch_id = "anomaly-batch-sha256-" + json_sha256(metadata)
    episodes: dict[tuple[str, ...], tuple[datetime, str, str]] = {}
    results = []
    for decision in decisions:
        point = indexed_points.get((*series_key(decision), decision.business_date))
        key = series_key(decision)
        episode_id = None
        if decision.alert:
            direction = (
                "positive"
                if (decision.residual_units or 0) > 0
                else "negative"
                if (decision.residual_units or 0) < 0
                else "zero"
            )
            previous = episodes.get(key)
            stamp = datetime.combine(decision.business_date, datetime.min.time(), UTC)
            if (
                previous is not None
                and previous[0] + timedelta(days=1) == stamp
                and previous[1] == direction
            ):
                episode_id = previous[2]
            else:
                episode_id = "signal-episode-sha256-" + json_sha256(
                    {
                        "batch_id": batch_id,
                        "scope": key,
                        "first_date": decision.business_date.isoformat(),
                        "direction": direction,
                    }
                )
            episodes[key] = (stamp, direction, episode_id)
        else:
            episodes.pop(key, None)
        kind = None
        if decision.status == "insufficient_data" and decision.input_status in {
            "day_unqualified",
            "no_declaration",
        }:
            kind = "data_quality_suspicion"
        elif decision.alert:
            kind = (
                "return_spike"
                if decision.event_type == "return_completed"
                else "stockout_censored_demand"
                if decision.on_hand == 0 and (decision.residual_units or 0) < 0
                else "sales_spike"
                if (decision.residual_units or 0) > 0
                else "sales_drop"
                if (decision.residual_units or 0) < 0
                else "residual_outlier"
            )
        data = decision.model_dump(mode="json")
        results.append(
            Item.model_validate_json(
                canonical_json(
                    {
                        **data,
                        "anomaly_id": "anomaly-sha256-"
                        + json_sha256({"batch_id": batch_id, "decision": data}),
                        "batch_id": batch_id,
                        "inference_run_id": batch_id,
                        "observed_window": {
                            "start": decision.business_date.isoformat(),
                            "end": decision.business_date.isoformat(),
                        },
                        "detected_at": generated_at.isoformat(),
                        "inventory_context": {
                            "stock_location_id": point.context.stock_location_id if point else None,
                            "on_hand": decision.on_hand,
                            "status": point.context.stock_status if point else "unavailable",
                        },
                        "promotion_context": {
                            "offered": decision.promotion_offered,
                            "planned_price": point.context.planned_price if point else None,
                        },
                        "signal_episode_id": episode_id,
                        "detector_version": release.binding.model_version,
                        "release_id": release.release_id,
                        "source_dataset_id": desc.coverage.descriptor.source_dataset_id,
                        "curated_dataset_id": frame.replay_manifest.descriptor.parent.curated_dataset_id,
                        "qualified_anomaly_input_id": frame.manifest.qualified_anomaly_input_id,
                        "full_dq_replay_id": desc.full_dq_replay_id,
                        "generated_at": generated_at.isoformat(),
                        "as_of": as_of.isoformat(),
                        "freshness_status": "unknown"
                        if decision.status == "insufficient_data"
                        else "stale"
                        if generated_at - decision.scoring_origin > timedelta(days=7)
                        else "current",
                        "anomaly_type": kind,
                        "alert_status": "insufficient_data"
                        if decision.status == "insufficient_data"
                        else "open"
                        if decision.alert
                        else "no_alert",
                        "score_definition": "absolute_causal_standardized_residual"
                        if qualification.model_family == "seasonal_residual"
                        else "portable_isolation_forest_path_length",
                    }
                )
            )
        )
    raw = b"".join(canonical_json(item.model_dump(mode="json")) + b"\n" for item in results)
    if len(raw) > 32 * 1024**2:
        raise ValueError("anomaly_batch_output_byte_budget")
    return {
        "batch_id": batch_id,
        "descriptor": metadata,
        "rows_sha256": hashlib.sha256(raw).hexdigest(),
        "generated_at": generated_at.isoformat(),
    }, results
