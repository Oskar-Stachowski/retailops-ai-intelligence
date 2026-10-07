"""Review rules for recorded native output; no quantity or deployment attestation."""

import asyncio
import json
from datetime import timedelta

import pytest
from test_agent_tools import NOW, policy
from test_native_read_tools import adapter, auth, request, risk_page

from retailops_ai.agent.execution import ToolExecutor
from retailops_ai.agent.suggestions import SuggestionCandidate, SuggestionPolicy, candidates


def native_candidates(*, state="scored", clock=NOW, opted=True, probability=None, read_delay=0):
    tool, reader = adapter("risk", state=state)
    if probability is not None:
        raw = risk_page(state).model_dump(mode="json")
        raw["items"][0]["probability"] = probability
        reader.value = type(reader.value).model_validate_json(json.dumps(raw))
    if read_delay:
        read_at = NOW + timedelta(seconds=read_delay)
        raw = reader.value.model_dump(mode="json")
        raw["generated_at"] = read_at.isoformat()
        raw["items"][0].update(
            read_at=read_at.isoformat(),
            origin_age_seconds=float(read_delay),
            output_age_seconds=float(read_delay),
        )
        reader.value = type(reader.value).model_validate_json(json.dumps(raw))
    authority, token = auth()
    if read_delay:
        from retailops_ai.security.local import LocalAccess
        from retailops_ai.security.models import AccessPolicy

        raw = authority._policy.model_dump(mode="json")
        raw["credentials"][0]["expires_at"] = (NOW + timedelta(hours=3)).isoformat()
        authority = LocalAccess(AccessPolicy.model_validate_json(json.dumps(raw)))
    executor = ToolExecutor(
        authority,
        {"get_stockout_risk": tool},
        policy(),
        "test",
        clock=lambda: NOW + timedelta(seconds=read_delay),
    )
    session = executor.open_session(token, default_scope=request("risk").scope)
    asyncio.run(session.execute_json(request("risk").model_dump_json()))
    executor.clock = lambda: clock
    proposal = (
        SuggestionPolicy(
            schema_version="1.0",
            policy_version="read-only-review-native-v2",
            native_stockout_review=True,
        )
        if opted
        else SuggestionPolicy(schema_version="1.0")
    )
    return candidates(session, proposal)


def test_native_review_requires_explicit_opt_in_and_preserves_origin():
    assert native_candidates(opted=False) == ()
    values = native_candidates()
    assert len(values) == 1
    row = values[0]
    assert row.source_as_of == NOW and row.evidence_observed_at == NOW
    assert row.expires_at == NOW + timedelta(seconds=300)
    assert row.requires_human_review and row.status == "proposed"
    assert len(row.evidence_refs) == len(row.model_release_refs) == 1
    assert "current stock" in row.rationale
    assert "quantity" not in SuggestionCandidate.model_fields
    assert "execute" not in SuggestionCandidate.model_fields
    assert native_candidates(clock=NOW + timedelta(seconds=300)) == ()


@pytest.mark.parametrize("state", ["already_stockout", "insufficient_data"])
def test_native_known_stockout_and_unknown_probability_are_distinct(state):
    assert len(native_candidates(state=state)) == (1 if state == "already_stockout" else 0)


def test_policy_cannot_enable_native_rule_under_old_version():
    with pytest.raises(ValueError, match="explicit_v2"):
        SuggestionPolicy(schema_version="1.0", native_stockout_review=True)
    with pytest.raises(ValueError, match="explicit_v2"):
        SuggestionPolicy(schema_version="1.0", policy_version="read-only-review-native-v2")


def test_native_observation_cannot_relabel_an_old_origin_or_extend_expiry():
    row = native_candidates()[0]
    for fields in (
        {"source_as_of": NOW - timedelta(days=2)},
        {"evidence_observed_at": None},
        {"expires_at": NOW + timedelta(seconds=301)},
    ):
        raw = row.model_copy(update=fields).model_dump(mode="json", exclude={"candidate_id"})
        from retailops_ai.data_contracts.identity import canonical_sha256

        raw["candidate_id"] = "candidate-sha256-" + canonical_sha256(raw)
        with pytest.raises(ValueError):
            SuggestionCandidate.model_validate_json(json.dumps(raw))


def test_recent_read_preserves_one_hour_old_origin_and_uses_observation_expiry():
    row = native_candidates(clock=NOW + timedelta(hours=1), read_delay=3600)[0]
    assert row.source_as_of == NOW
    assert row.evidence_observed_at == NOW + timedelta(hours=1)
    assert row.expires_at == row.evidence_observed_at + timedelta(seconds=300)
    assert native_candidates(clock=NOW + timedelta(hours=1, seconds=300), read_delay=3600) == ()


def test_proposed_native_threshold_does_not_create_a_review_below_its_value():
    assert native_candidates(probability=0.79) == ()
