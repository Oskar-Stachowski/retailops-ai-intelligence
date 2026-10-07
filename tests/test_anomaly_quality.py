"""Qualification rejects empty, incomplete and resealed inconsistent reports."""

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.anomaly_evaluation.contract import Decision, Episode, Truth, TruthWindow
from retailops_ai.anomaly_evaluation.evaluator import evaluate
from retailops_ai.anomaly_evaluation.quality import QualityPolicy, assess, combine
from retailops_ai.source_snapshot.files import json_sha256

WINDOW = Window(start=date(2026, 1, 1), end=date(2026, 1, 10))
AS_OF = datetime(2026, 2, 1, tzinfo=UTC)
SCOPE = dict(
    product_id="00000000-0000-0000-0000-000000000001",
    selling_location_id="00000000-0000-0000-0000-000000000002",
    channel="online",
    currency="PLN",
)
POLICY = QualityPolicy(
    minimum_precision=0.5,
    minimum_recall=0.5,
    maximum_false_alerts_per_1000=100,
    minimum_high_severity_precision=0.5,
    minimum_episode_recall=0.5,
    minimum_evaluable_coverage=0.5,
    minimum_clean_per_case=1,
)


def cases(*, alerts=True, inputs=None):
    result = []
    for seed in (42, 137, 2026):
        for scenario in ("demand", "physical"):
            source = "source-sha256-" + json_sha256({"seed": seed, "scenario": scenario})
            definitions = (
                [
                    ("sale_completed", 2, "one_day_spike"),
                    ("sale_completed", 4, "multi_day_spike"),
                    ("sale_completed", 6, "sustained_drop"),
                ]
                if scenario == "demand"
                else [
                    ("return_completed", 2, "return_spike"),
                    ("sale_completed", 4, "inventory_censored_episode"),
                ]
            )
            episodes = tuple(
                Episode(
                    **SCOPE,
                    event_type=event,
                    episode_id=f"{seed}-{scenario}-{kind}",
                    business_type=kind,
                    window=Window(start=date(2026, 1, day), end=date(2026, 1, day)),
                    first_evidence_available_at=datetime(
                        2026, 1, day + (2 if event == "sale_completed" else 4), tzinfo=UTC
                    ),
                    label_available_at=AS_OF,
                )
                for event, day, kind in definitions
            )
            truth = Truth(
                source_dataset_id=source,
                source_scenario_sha256="b" * 64,
                episodes=episodes,
                complete_windows=tuple(
                    TruthWindow(**SCOPE, event_type=event, window=WINDOW, available_at=AS_OF)
                    for event in ("sale_completed", "return_completed")
                ),
            )
            rows = []
            for event in ("sale_completed", "return_completed"):
                for day in range(1, 11):
                    alert = alerts and any((event, day) == (e, d) for e, d, _ in definitions)
                    rows.append(
                        Decision(
                            **SCOPE,
                            event_type=event,
                            business_date=date(2026, 1, day),
                            scoring_origin=datetime(2026, 1, day, tzinfo=UTC)
                            + timedelta(days=2 if event == "sale_completed" else 4),
                            detector_id="anomaly-detector-sha256-" + "a" * 64,
                            family="seasonal_residual",
                            role="final_test",
                            status="scored",
                            score=2.0 if alert else 0.0,
                            threshold=1.0,
                            alert=alert,
                            severity="high" if alert else "none",
                            explanation_codes=(),
                            input_status="ready_input",
                            observed_units=10,
                            expected_units=10,
                            residual_units=0,
                            promotion_offered=False,
                            on_hand=10,
                        )
                    )
            report = evaluate(rows, truth, WINDOW, AS_OF, source_dataset_id=source)
            if inputs is not None:
                inputs[f"evaluation_inputs_{seed}_{scenario}.json"] = {
                    "seed": seed,
                    "scenario": scenario,
                    "source_dataset_id": source,
                    "decisions": [d.model_dump(mode="json") for d in rows],
                    "truth": truth.model_dump(mode="json"),
                    "window": WINDOW.model_dump(mode="json"),
                    "as_of": AS_OF.isoformat(),
                }
            result.append(
                dict(seed=seed, scenario=scenario, source_dataset_id=source, report=report)
            )
    return result


def reseal(case):
    report = case["report"]
    report["evaluation_id"] = "anomaly-evaluation-sha256-" + json_sha256(report["descriptor"])


def test_full_inventory_and_sample_gate_pass_only_with_actual_metrics():
    report = assess(cases(), POLICY)
    assert report["descriptor"]["status"] == "passed"
    assert report["descriptor"]["summary"]["metrics"]["episode_recall"] == 1
    assert report["descriptor"]["summary"]["episodes_per_type"]["return_spike"] == 3
    assert assess(cases()[:-1], POLICY)["descriptor"]["status"] == "not_ready"


def test_no_alerts_does_not_qualify_or_claim_perfect_precision():
    report = assess(cases(alerts=False), POLICY)["descriptor"]
    assert report["status"] == "not_ready"
    assert report["summary"]["metrics"]["precision"] is None
    assert report["summary"]["metrics"]["episode_recall"] == 0


def test_good_global_recall_cannot_hide_a_wholly_missed_business_type():
    inputs = {}
    data = cases(inputs=inputs)
    for case in data:
        value = inputs[f"evaluation_inputs_{case['seed']}_{case['scenario']}.json"]
        decisions = [Decision.model_validate_json(json.dumps(d)) for d in value["decisions"]]
        if case["scenario"] == "physical":
            decisions = [
                d.model_copy(update={"score": 0.0, "alert": False, "severity": "none"})
                if d.event_type == "return_completed" and d.business_date.day == 2
                else d
                for d in decisions
            ]
        case["report"] = evaluate(
            decisions,
            Truth.model_validate_json(json.dumps(value["truth"])),
            WINDOW,
            AS_OF,
            source_dataset_id=case["source_dataset_id"],
        )
    report = assess(data, POLICY)["descriptor"]
    assert report["summary"]["metrics"]["episode_recall"] == 0.8
    assert report["status"] == "not_ready"
    check = next(c for c in report["checks"] if c["check_id"] == "episode_recall/return_spike")
    assert check["status"] == "failed" and check["value"] == 0


@pytest.mark.parametrize(
    "target", ["observation", "coverage", "segment", "episode", "high_severity"]
)
def test_even_resealed_inconsistent_evidence_is_rejected(target):
    data = deepcopy(cases())
    desc = data[0]["report"]["descriptor"]
    if target == "observation":
        desc["observation"]["true_positive"] += 1
    elif target == "coverage":
        desc["coverage"]["unknown_truth"] += 1
    elif target == "segment":
        desc["per_segment"]["sale_completed/PLN"]["true_positive"] += 1
    elif target == "episode":
        desc["episode"]["details"][0]["alert_count"] = 0
    else:
        desc["observation"]["high_severity_precision"]["numerator"] += 1
    reseal(data[0])
    with pytest.raises(ValueError):
        combine(data)


def test_duplicate_case_and_mixed_model_evidence_are_rejected():
    data = cases()
    with pytest.raises(ValueError, match="duplicate_case"):
        combine([data[0], data[0]])
    data[1]["report"]["descriptor"]["detector_id"] = "anomaly-detector-sha256-" + "f" * 64
    reseal(data[1])
    with pytest.raises(ValueError, match="one_saved_model"):
        combine(data)


@pytest.mark.parametrize(
    "field",
    [
        "minimum_precision",
        "minimum_recall",
        "minimum_high_severity_precision",
        "minimum_episode_recall",
    ],
)
def test_zero_quality_gate_is_not_a_qualification_policy(field):
    with pytest.raises(ValidationError):
        QualityPolicy.model_validate({**POLICY.model_dump(), field: 0})


def qualification_artifacts():
    import hashlib

    from retailops_ai.source_snapshot.files import canonical_json

    inputs = {}
    data = cases(inputs=inputs)
    raw = {name: canonical_json(value) + b"\n" for name, value in inputs.items()}
    first = next(iter(inputs.values()))["decisions"]
    scopes = [
        {k: d[k] for k in (*SCOPE, "event_type")}
        for d in first
        if d["business_date"] == "2026-01-01"
    ]
    frozen = {
        "detector_id": "anomaly-detector-sha256-" + "a" * 64,
        "family": "seasonal_residual",
        "final_test_at_freeze": "not_scored",
        "quality_policy": POLICY.model_dump(mode="json"),
        "evaluation_policy": data[0]["report"]["descriptor"]["policy"],
        "protocol": {"test": WINDOW.model_dump(mode="json"), "scopes": scopes},
        "final_as_of": AS_OF.isoformat(),
        "data_inventory": [
            {k: case[k] for k in ("seed", "scenario", "source_dataset_id")} for case in data
        ],
    }
    config = {
        "selection": {
            "selection_id": "anomaly-selection-sha256-" + json_sha256(frozen),
            "descriptor": frozen,
        }
    }
    gate = {
        "quality": assess(data, POLICY),
        "evaluation_inputs": {
            name: {"sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value)}
            for name, value in raw.items()
        },
    }
    return config, gate, raw


def test_registry_quality_is_recomputed_from_saved_decisions_and_truth():
    from retailops_ai.anomaly_evaluation.verification import verify_quality

    config, gate, raw = qualification_artifacts()
    actual = verify_quality(
        gate, config, raw.__getitem__, "anomaly-detector-sha256-" + "a" * 64, "seasonal_residual"
    )
    assert actual == gate["quality"]
    gate["quality"]["descriptor"]["summary"]["metrics"]["precision"] = 0.9
    gate["quality"]["quality_id"] = "anomaly-quality-sha256-" + json_sha256(
        gate["quality"]["descriptor"]
    )
    with pytest.raises(ValueError, match="recomputed_gate_failed"):
        verify_quality(
            gate,
            config,
            raw.__getitem__,
            "anomaly-detector-sha256-" + "a" * 64,
            "seasonal_residual",
        )


@pytest.mark.parametrize("fault", ["checksum", "missing_case", "scope", "model", "clock"])
def test_registry_rejects_unbound_or_incomplete_evaluation_inputs(fault):
    import hashlib
    import json

    from retailops_ai.anomaly_evaluation.verification import verify_quality
    from retailops_ai.source_snapshot.files import canonical_json

    config, gate, raw = qualification_artifacts()
    name = "evaluation_inputs_42_demand.json"
    if fault == "missing_case":
        del gate["evaluation_inputs"][name]
    elif fault == "checksum":
        raw[name] += b" "
    else:
        value = json.loads(raw[name])
        if fault == "scope":
            value["decisions"] = [
                d for d in value["decisions"] if d["event_type"] == "sale_completed"
            ]
        elif fault == "model":
            value["decisions"][0]["detector_id"] = "anomaly-detector-sha256-" + "f" * 64
        else:
            value["decisions"][0]["scoring_origin"] = "2026-01-05T00:00:00Z"
        raw[name] = canonical_json(value) + b"\n"
        gate["evaluation_inputs"][name] = {
            "sha256": hashlib.sha256(raw[name]).hexdigest(),
            "size_bytes": len(raw[name]),
        }
    with pytest.raises(ValueError):
        verify_quality(
            gate,
            config,
            raw.__getitem__,
            "anomaly-detector-sha256-" + "a" * 64,
            "seasonal_residual",
        )
