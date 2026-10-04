"""Recompute qualification from saved decisions and independent offline truth."""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from retailops_ai.anomaly_detectors.protocol import Scope, Window, scoring_origin, series_key
from retailops_ai.anomaly_evaluation.contract import Decision, EvaluationPolicy, Truth
from retailops_ai.anomaly_evaluation.evaluator import evaluate
from retailops_ai.anomaly_evaluation.quality import QualityPolicy, assess
from retailops_ai.qualified_anomalies.contract import Policy
from retailops_ai.source_snapshot.files import decode_json, json_sha256


def verify_quality(
    gate: dict[str, Any],
    config: dict[str, Any],
    artifact: Callable[[str], bytes],
    detector_id: str,
    family: str,
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
