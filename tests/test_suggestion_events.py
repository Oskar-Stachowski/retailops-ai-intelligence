"""Independent Source wire/schema and service identity checks for review events."""

import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from retailops_ai.assistant.contracts import PersistedSuggestion
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.intelligence_events.contracts import EVENT_NAMESPACE
from retailops_ai.intelligence_events.suggestion_contracts import (
    RecommendationGenerated,
    suggestion_event,
)

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "contracts/events/suggestion-source-v1"


@pytest.fixture
def source_event():
    return json.loads((FOLDER / "recommendation_generated.fixture.json").read_bytes())


def test_emitter_matches_independently_pinned_source_fixture(source_event):
    event = suggestion_event(
        PersistedSuggestion.model_validate_json(json.dumps(source_event["payload"]))
    )
    assert event.model_dump(mode="json") == source_event
    Draft202012Validator(
        json.loads((FOLDER / "recommendation_generated.schema.json").read_bytes()),
        format_checker=FormatChecker(),
    ).validate(event.model_dump(mode="json"))
    assert event.partition_key == canonical_sha256(
        {k: source_event["payload"][k] for k in ("product_id", "selling_location_id", "channel")}
    )
    assert json.loads(event.wire_bytes()) == source_event


@pytest.mark.parametrize("field", ["event_id", "correlation_id", "occurred_at", "ingested_at"])
def test_envelope_cannot_change_identity_or_time(source_event, field):
    raw = deepcopy(source_event)
    raw[field] = str(uuid4()) if field.endswith("id") else "2026-10-04T18:00:02Z"
    with pytest.raises(ValueError):
        RecommendationGenerated.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field", ["recommendation_id", "answer_id", "store_id"])
def test_transport_requires_service_uuid_and_scope_bindings(source_event, field):
    raw = source_event["payload"]
    raw[field] = str(uuid4())
    with pytest.raises(ValueError):
        suggestion_event(PersistedSuggestion.model_validate_json(json.dumps(raw)))


@pytest.mark.parametrize("change", ["v2", "boolean", "future_source", "candidate_hash", "extra"])
def test_unaccepted_or_forged_payload_is_not_downgraded(source_event, change):
    raw = source_event["payload"]
    if change == "v2":
        raw["policy_version"] = "read-only-review-native-v2"
        raw["evidence_observed_at"] = raw["source_as_of"]
        base = {k: raw[k] for k in PersistedSuggestion.model_fields if k in raw}
        for key in (
            "recommendation_id",
            "trace_id",
            "answer_id",
            "created_at",
            "origin",
            "store_id",
            "summary",
            "agent_config_version",
            "candidate_id",
        ):
            base.pop(key, None)
        raw["candidate_id"] = "candidate-sha256-" + canonical_sha256(base)
        raw["recommendation_id"] = str(uuid5(UUID(raw["trace_id"]), raw["candidate_id"]))
        assert (
            PersistedSuggestion.model_validate_json(json.dumps(raw)).policy_version
            == "read-only-review-native-v2"
        )
    elif change == "boolean":
        raw["requires_human_review"] = 1
    elif change == "future_source":
        raw["created_at"] = "2026-10-04T17:59:59Z"
    elif change == "candidate_hash":
        raw["rationale"] = "Forged assertion"
    else:
        raw["automatic_order"] = True
    with pytest.raises(ValueError):
        suggestion_event(PersistedSuggestion.model_validate_json(json.dumps(raw)))


def test_event_id_uses_shared_v2_namespace(source_event):
    event = RecommendationGenerated.model_validate_json(json.dumps(source_event))
    assert event.event_id == uuid5(
        EVENT_NAMESPACE, "recommendation_generated:" + str(event.payload.recommendation_id)
    )


def test_outbox_configuration_is_explicit_and_requires_sql():
    assert not Settings(
        APP_ENV="test", ARTIFACT_ROOT="./artifacts"
    ).assistant_suggestion_outbox_enabled
    with pytest.raises(ValueError, match="suggestion_outbox_requires_database"):
        Settings(
            APP_ENV="test", ARTIFACT_ROOT="./artifacts", ASSISTANT_SUGGESTION_OUTBOX_ENABLED=True
        )
