"""Model envelope mechanics preserve native API payloads; fixtures do not qualify models."""

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.intelligence_events.model_contracts import (
    AnomalyDetected,
    StockoutRiskScored,
    anomaly_event,
    stockout_event,
)

ROOT = Path(__file__).resolve().parents[1] / "contracts/events/v2"


@pytest.fixture
def model_events():
    # Envelope validation needs the existing explicit contract fixtures, not an
    # unrelated training fit in a long-lived shard with cumulative process RSS.
    # Genuine original-model publication is exercised by the dedicated native CI.
    return (
        AnomalyDetected.model_validate_json((ROOT / "anomaly_detected.fixture.json").read_bytes()),
        StockoutRiskScored.model_validate_json(
            (ROOT / "stockout_risk_scored.fixture.json").read_bytes()
        ),
    )


def test_generated_schemas_and_envelopes_preserve_native_payloads(model_events):
    for event in model_events:
        schema = type(event).model_json_schema()
        assert (ROOT / (event.event_type + ".schema.json")).read_bytes() == (
            canonical_bytes(schema) + b"\n"
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            event.model_dump(mode="json")
        )
        assert type(event).model_validate_json(event.model_dump_json()) == event
        assert event.occurred_at == event.ingested_at == event.payload.generated_at
        assert event.correlation_id == event.payload.inference_run_id
    anomaly, risk = model_events
    assert anomaly_event(anomaly.payload) == anomaly
    assert stockout_event(risk.payload) == risk
    assert anomaly.partition_key != risk.partition_key
    assert risk.payload.quality_status == "mechanics_only"
    assert risk.payload.lineage.source_watermark is None
    assert risk.payload.freshness_status == "unknown"
    # Extending supported types must not rewrite the accepted forecast schema.
    assert hashlib.sha256((ROOT / "forecast_generated.schema.json").read_bytes()).hexdigest() == (
        "17e9a9c05f6f39101e6a00dbfed89203800dd388818ee7b0b9cf7a87c05af511"
    )


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize(
    "change", ["event_id", "correlation", "time", "topic", "grain", "result_id"]
)
def test_forged_envelope_or_native_identity_is_rejected(model_events, index, change):
    event = model_events[index]
    raw = event.model_dump(mode="json")
    if change == "event_id":
        raw["event_id"] = "11111111-1111-4111-8111-111111111111"
    elif change == "correlation":
        raw["correlation_id"] = "run-" + "f" * 32
    elif change == "time":
        raw["occurred_at"] = "2026-09-02T00:00:00Z"
    elif change == "topic":
        raw["topic"] = "retailops.events.v1"
    elif change == "grain":
        raw["payload"]["product_id"] = "foreign-product"
    else:
        identity = "anomaly_id" if index == 0 else "risk_id"
        prefix = "anomaly-sha256-" if index == 0 else "risk-sha256-"
        raw["payload"][identity] = prefix + "f" * 64
    with pytest.raises(ValueError):
        type(event).model_validate_json(canonical_bytes(raw))


def test_copied_native_objects_are_revalidated_before_enqueuing(model_events):
    anomaly, risk = model_events
    with pytest.raises(ValueError):
        anomaly_event(anomaly.payload.model_copy(update={"score": -1.0}))
    with pytest.raises(ValueError):
        stockout_event(risk.payload.model_copy(update={"probability": 2.0}))
    raw = risk.model_dump(mode="json")
    raw["payload"]["status"] = "insufficient_data"
    raw["payload"]["probability"] = 0.1
    with pytest.raises(ValueError):
        StockoutRiskScored.model_validate_json(json.dumps(raw))
    raw = anomaly.model_dump(mode="json")
    raw["payload"]["alert_status"] = "insufficient_data"
    with pytest.raises(ValueError):
        AnomalyDetected.model_validate_json(json.dumps(raw))
