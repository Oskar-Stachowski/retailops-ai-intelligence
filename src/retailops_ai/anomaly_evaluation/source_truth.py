"""Offline-only truth adapter; the scoring/training modules never import it."""

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

from retailops_ai.anomaly_detectors.protocol import (
    EventType,
    Scope,
    Window,
    scoring_origin,
    series_key,
)
from retailops_ai.anomaly_evaluation.contract import Episode, Truth, TruthWindow
from retailops_ai.qualified_anomalies.contract import Policy
from retailops_ai.source_snapshot.files import decode_json, json_sha256, read_bytes

TRUTH_POLICY = {
    "version": "anomaly-portfolio-truth-adapter-1.0.0",
    "positive": "primary_injection_scope_and_declared_business_window",
    "clean": "products_without_intervention_started_by_census_end",
    "spillover": "all_other_slots_of_intervened_products_unknown_and_counted",
    "outcome_clock": "closed_day_plus_public_event_availability_delay",
    "training_truth": "never_accessed_by_training_or_scoring",
}


def source_truth(
    scenario_path: Path,
    verified_source: dict[str, Any],
    scopes: tuple[Scope, ...],
    window: Window,
) -> Truth:
    """Join independently verified private truth with the public scope census.

    Interventions can alter later stock and returns or other channels of a SKU.
    No such observation is labelled clean merely because its primary injection
    has ended. This conservative, fixed rule uses intervention scope, never a
    detector's score or a clean-label training filter.
    """
    raw = read_bytes(scenario_path.parent, scenario_path.name, 16 * 1024**2)
    receipt = verified_source["scenario"]
    document = decode_json(raw)
    source_id = verified_source["dataset_id"]
    if (
        (len(raw), hashlib.sha256(raw).hexdigest()) != (receipt["size_bytes"], receipt["sha256"])
        or document.get("data_class") != "simulation_truth"
        or json_sha256(document["plan"]) != verified_source["descriptor"]["scenario_plan_sha256"]
        or source_id != "source-sha256-" + json_sha256(verified_source["descriptor"])
    ):
        raise ValueError("anomaly_truth_native_source_binding")
    plan = document["plan"]
    injected_products = {
        item["product_id"]
        for item in plan["injections"]
        if date.fromisoformat(item["start_date"]) <= window.end
    }
    episodes = []
    for item in plan["injections"]:
        event: EventType = (
            "return_completed" if item["injection_type"] == "return_spike" else "sale_completed"
        )
        candidates = [
            s
            for s in scopes
            if s.event_type == event
            and (s.product_id, s.selling_location_id, s.channel)
            == (item["product_id"], item["selling_location_id"], item["channel"])
        ]
        if len(candidates) != 1:
            raise ValueError("anomaly_truth_scope_currency_ambiguous_or_absent")
        interval = Window(
            start=date.fromisoformat(item["start_date"]), end=date.fromisoformat(item["end_date"])
        )
        if interval.end < window.start or interval.start > window.end:
            continue
        if interval.start < window.start or interval.end > window.end:
            raise ValueError("anomaly_truth_episode_split_boundary")
        episodes.append(
            Episode(
                **candidates[0].model_dump(),
                episode_id=item["id"],
                business_type=item["injection_type"],
                window=interval,
                first_evidence_available_at=scoring_origin(interval.start, event, Policy()),
                label_available_at=scoring_origin(interval.end, event, Policy()),
            )
        )
    complete = []
    for scope in scopes:
        if scope.product_id not in injected_products:
            complete.append(
                TruthWindow(
                    **scope.model_dump(),
                    window=window,
                    available_at=scoring_origin(window.end, scope.event_type, Policy()),
                )
            )
        else:
            occupied = [e for e in episodes if series_key(e) == series_key(scope)]
            # Post-episode spillover is unknown, including the late-tolerance
            # day. Unknown truth cannot be turned into a clean negative merely
            # to increase matching coverage.
            for episode in occupied:
                complete.append(
                    TruthWindow(
                        **scope.model_dump(),
                        window=episode.window,
                        available_at=episode.label_available_at,
                    )
                )
    return Truth(
        source_dataset_id=source_id,
        source_scenario_sha256=hashlib.sha256(raw).hexdigest(),
        complete_windows=tuple(complete),
        episodes=tuple(episodes),
    )
