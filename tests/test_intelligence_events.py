"""Contract pin, stable replay identities, and verified-publication-only generation."""

import json
from pathlib import Path

import pytest
from intelligence_fixture import fixture_event
from jsonschema import Draft202012Validator, FormatChecker
from test_v12_publication import (
    artifacts as artifacts,
)
from test_v12_publication import completed as completed
from test_v12_publication import (
    inputs as inputs,
)
from test_v12_publication import (
    loaded as loaded,
)
from test_v12_publication import (
    prepared_input as prepared_input,
)
from test_v12_publication import (
    qualification as qualification,
)
from test_v12_publication import (
    serving as serving,
)
from test_v12_publication import (
    tables as tables,
)
from test_v12_publication import (
    timeline as timeline,
)

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.intelligence_events.contracts import ForecastGenerated, forecast_events

ROOT = Path(__file__).resolve().parents[1] / "contracts/events/v2"


def test_schema_is_generated_from_the_ml_api_model_and_fixture_is_explicit():
    schema = ForecastGenerated.model_json_schema()
    assert (ROOT / "forecast_generated.schema.json").read_bytes() == canonical_bytes(schema) + b"\n"
    example = json.loads((ROOT / "forecast_generated.fixture.json").read_bytes())
    event = ForecastGenerated.model_validate_json(canonical_bytes(example))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
    assert event.payload.model_name.endswith("-mechanics")
    assert event.payload.freshness.status == "unknown"


def test_retry_has_stable_event_result_and_grain_identities():
    first, second = fixture_event(), fixture_event()
    assert first == second
    assert first.event_id == second.event_id
    later = fixture_event(suffix="b")
    assert later.event_id != first.event_id and later.partition_key == first.partition_key
    assert fixture_event(product_id="other-product").partition_key != first.partition_key


@pytest.mark.parametrize(
    "change", ["id", "topic", "minor", "grain", "reference", "time", "freshness"]
)
def test_invalid_envelope_or_payload_binding_is_rejected(change):
    raw = fixture_event().model_dump(mode="json")
    if change == "id":
        raw["event_id"] = "11111111-1111-4111-8111-111111111111"
    elif change == "topic":
        raw["topic"] = "retailops.intelligence.v1"
    elif change == "minor":
        raw["schema_version"] = "2.1"
    elif change == "grain":
        raw["payload"]["target_date"] = "2026-11-01"
    elif change == "reference":
        raw["payload"]["prediction"]["candidate"]["median"] = 11.0
    elif change == "time":
        raw["occurred_at"] = "2026-10-02T00:01:00Z"
    else:
        raw["payload"]["freshness"]["status"] = "current"
    with pytest.raises(ValueError):
        ForecastGenerated.model_validate_json(canonical_bytes(raw))


def test_complete_publication_preserves_every_functional_and_stable_event(completed):
    output, run, receipt = completed
    events = forecast_events(output, run, receipt)
    assert events == forecast_events(output, run, receipt)
    assert len(events) == len(output.rows)
    assert sorted(e.payload.prediction.model_dump_json() for e in events) == sorted(
        row.prediction.model_dump_json() for row in output.rows
    )
    with pytest.raises(ValueError):
        forecast_events(output, run.model_copy(update={"status": "running"}), receipt)
