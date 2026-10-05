"""Recompute qualification from saved decisions and independent offline truth."""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from retailops_ai.anomaly_detectors.codec import baseline_score, forest_scores
from retailops_ai.anomaly_detectors.protocol import Scope, Window, scoring_origin, series_key
from retailops_ai.anomaly_detectors.rows import MultiscaleRow, validate_row
from retailops_ai.anomaly_evaluation.contract import Decision, EvaluationPolicy, Truth
from retailops_ai.anomaly_evaluation.evaluator import evaluate
from retailops_ai.anomaly_evaluation.quality import QualityPolicy, assess
from retailops_ai.qualified_anomalies.contract import Policy
from retailops_ai.source_snapshot.files import canonical_json, decode_json, json_sha256

if TYPE_CHECKING:
    from retailops_ai.anomaly_portfolio.model import Model


def verify_scores(model: "Model", decisions: list[Decision], inputs: list[Any]) -> None:
    """Replay the actual saved baseline/forest over the saved public model rows."""
    if len(inputs) != len(decisions):
        raise ValueError("anomaly_quality_scoring_input_census")
    from retailops_ai.anomaly_portfolio.model import MultiscaleDescriptor

    rows = [validate_row(r) if r is not None else None for r in inputs]
    if any(
        r is not None
        and isinstance(r, MultiscaleRow) != (isinstance(model.descriptor, MultiscaleDescriptor))
        for r in rows
    ):
        raise ValueError("anomaly_quality_model_row_recipe_binding")
    values: dict[int, tuple[float, Any]] = {}
    for group in model.descriptor.groups:
        selected = [
            (i, r)
            for i, (d, r) in enumerate(zip(decisions, rows, strict=True))
            if r is not None and (d.event_type, d.currency) == (group.event_type, group.currency)
        ]
        selected_rows = [r for _, r in selected]
        family = decisions[0].family
        threshold = (
            group.baseline_threshold if family == "seasonal_residual" else group.forest_threshold
        )
        if threshold is None or (family == "isolation_forest" and group.pipeline is None):
            continue
        computed = (
            [baseline_score(r) for r in selected_rows]
            if family == "seasonal_residual"
            else forest_scores(group.pipeline, selected_rows)
            if group.pipeline is not None
            else ()
        )
        for (index, _), value in zip(selected, computed, strict=True):
            values[index] = (value, threshold)
    for index, (d, r) in enumerate(zip(decisions, rows, strict=True)):
        if d.detector_id != model.detector_id or (
            r is not None
            and (
                d.input_status != "ready_input"
                or (
                    d.observed_units,
                    d.expected_units,
                    d.residual_units,
                    d.promotion_offered,
                    d.on_hand,
                )
                != (
                    r.observed_units,
                    r.expected_units,
                    r.residual_units,
                    r.promotion_offered,
                    r.on_hand,
                )
            )
        ):
            raise ValueError("anomaly_quality_scoring_input_binding")
        if index not in values:
            if d.status != "insufficient_data":
                raise ValueError("anomaly_quality_saved_model_abstention")
            continue
        value, threshold = values[index]
        alert = value > threshold.threshold
        severity = "none" if not alert else "high" if value > threshold.high_threshold else "medium"
        if (d.status, d.score, d.threshold, d.alert, d.severity) != (
            "scored",
            value,
            threshold.threshold,
            alert,
            severity,
        ):
            raise ValueError("anomaly_quality_saved_model_prediction_mismatch")


def verify_quality(
    gate: dict[str, Any],
    config: dict[str, Any],
    artifact: Callable[[str], bytes],
    detector_id: str,
    family: str,
    model: "Model | None" = None,
) -> dict[str, Any]:
    """A passed string or a resealed metrics document is insufficient evidence."""
    selection = config["selection"]
    frozen = selection["descriptor"]
    if (
        selection["selection_id"] != "anomaly-selection-sha256-" + json_sha256(frozen)
        or (frozen["detector_id"], frozen["family"]) != (detector_id, family)
        or frozen["final_test_at_freeze"] != "not_scored"
    ):
        raise ValueError("anomaly_quality_selection_binding")
    if model is not None and (
        model.detector_id != detector_id
        or hashlib.sha256(canonical_json(model.model_dump(mode="json")) + b"\n").hexdigest()
        != frozen["model_sha256"]
        or model.descriptor.train.model_dump(mode="json") != frozen["protocol"]["train"]
        or model.descriptor.validation.model_dump(mode="json") != frozen["protocol"]["validation"]
        or model.descriptor.training_cutoff.isoformat() != frozen["protocol"]["training_cutoff"]
        or model.descriptor.selection_cutoff.isoformat() != frozen["protocol"]["selection_cutoff"]
    ):
        raise ValueError("anomaly_quality_model_protocol_binding")
    policy = QualityPolicy.model_validate_json(json.dumps(frozen["quality_policy"]))
    scopes = {
        series_key(Scope.model_validate_json(json.dumps(scope)))
        for scope in frozen["protocol"]["scopes"]
    }
    evaluation_policy = EvaluationPolicy.model_validate_json(
        json.dumps(frozen["evaluation_policy"])
    )
    inventory = {(row["seed"], row["scenario"]): row for row in frozen["data_inventory"]}
    wanted = {
        (seed, scenario) for seed in policy.source_seeds for scenario in policy.source_scenarios
    }
    if set(inventory) != wanted or len(frozen["data_inventory"]) != len(wanted):
        raise ValueError("anomaly_quality_selection_inventory")
    receipts = gate["evaluation_inputs"]
    if set(receipts) != {f"evaluation_inputs_{seed}_{scenario}.json" for seed, scenario in wanted}:
        raise ValueError("anomaly_quality_artifact_inventory")
    cases = []
    for seed, scenario in sorted(wanted):
        name = f"evaluation_inputs_{seed}_{scenario}.json"
        raw = artifact(name)
        receipt = receipts[name]
        if (len(raw), hashlib.sha256(raw).hexdigest()) != (
            receipt["size_bytes"],
            receipt["sha256"],
        ):
            raise ValueError("anomaly_quality_input_checksum")
        value = decode_json(raw)
        truth = Truth.model_validate_json(json.dumps(value["truth"]))
        decisions = [Decision.model_validate_json(json.dumps(p)) for p in value["decisions"]]
        window = Window.model_validate_json(json.dumps(value["window"]))
        expected_source = inventory[seed, scenario]["source_dataset_id"]
        if (
            (value["seed"], value["scenario"], value["source_dataset_id"])
            != (seed, scenario, expected_source)
            or value["window"] != frozen["protocol"]["test"]
            or value["as_of"] != frozen["final_as_of"]
            or {series_key(d) for d in decisions} != scopes
            or any(
                d.scoring_origin != scoring_origin(d.business_date, d.event_type, Policy())
                for d in decisions
            )
            or any(
                (d.detector_id, d.family, d.role) != (detector_id, family, "final_test")
                for d in decisions
            )
        ):
            raise ValueError("anomaly_quality_final_test_binding")
        if model is not None:
            frozen_at = datetime.fromisoformat(frozen["frozen_at"])
            started = datetime.fromisoformat(value["final_started_at"])
            completed = datetime.fromisoformat(value["final_completed_at"])
            if (
                frozen_at.utcoffset() is None
                or not frozen_at < started <= completed
                or value["public_input_lineage"] != inventory[seed, scenario]
                or truth.source_scenario_sha256
                != inventory[seed, scenario]["source_scenario_sha256"]
            ):
                raise ValueError("anomaly_quality_final_time_or_lineage_binding")
            verify_scores(model, decisions, value["model_rows"])
        report = evaluate(
            decisions,
            truth,
            window,
            datetime.fromisoformat(value["as_of"]),
            evaluation_policy,
            source_dataset_id=expected_source,
        )
        cases.append(
            {
                "seed": seed,
                "scenario": scenario,
                "source_dataset_id": expected_source,
                "report": report,
            }
        )
    quality = assess(cases, policy)
    if quality != gate["quality"] or quality["descriptor"]["status"] != "passed":
        raise ValueError("anomaly_quality_recomputed_gate_failed")
    return quality
