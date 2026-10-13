import json
from datetime import UTC, datetime

import pytest
from test_agent_graph import fixture_outputs, harness, invoke, request, settings

from retailops_ai.agent.chat_contracts import AnswerDraft
from retailops_ai.agent.evidence import EvidencePolicy
from retailops_ai.agent.graph_contracts import GraphRequest
from retailops_ai.agent.suggestions import SuggestionCandidate
from retailops_ai.agent.tools import OUTPUT
from retailops_ai.data_contracts.identity import canonical_sha256


def qualified(horizon_end="2026-08-25", **changes):
    query = request("recommendations", window={"start": "2026-08-25", "end": horizon_end})
    cases = []
    for call, output in fixture_outputs(query):
        raw = output.model_dump(mode="json")
        if call.tool == "get_stockout_risk":
            raw["items"][0].update(
                probability=0.9, inventory_source_ref="fixture-get_inventory_status"
            )
        elif call.tool == "get_model_status":
            raw["items"][0]["deployed_release_ref"] = "fixture-calibrated-risk-release-v1"
        elif call.tool == "get_demand_forecast":
            raw["result"]["items"][0]["generated_at"] = "2026-08-23T00:00:00Z"
        if call.tool in changes:
            changes[call.tool](raw)
        if call.tool == "get_demand_forecast":
            for row in raw["result"]["items"]:
                row["prediction_id"] = "prediction-sha256-" + canonical_sha256(
                    {
                        key: value
                        for key, value in row.items()
                        if key
                        not in {
                            "prediction_id",
                            "prediction_dataset_id",
                            "generated_at",
                            "freshness_status",
                        }
                    }
                )
        cases.append((call, OUTPUT.validate_json(json.dumps(raw))))
    return query, cases


def test_review_requires_all_four_sources_and_has_stable_identity_and_expiry():
    query, cases = qualified()
    runner, bearer, query = harness(query, cases=cases)
    first = invoke(runner, bearer, query)
    second = invoke(runner, bearer, query)
    assert first.status == "succeeded" and len(first.suggestions) == 1
    candidate = first.suggestions[0]
    assert candidate == second.suggestions[0]
    assert candidate.recommendation_type == "review_replenishment"
    assert candidate.stock_location_id == "warehouse-01"
    assert len(candidate.evidence_refs) == 4
    assert candidate.requires_human_review and candidate.status == "proposed"
    assert (candidate.expires_at - candidate.source_as_of).total_seconds() == 300
    assert first.answer.recommended_actions == [candidate.draft_action()]
    assert first.trace.tool_calls == 4
    assert "quantity" not in SuggestionCandidate.model_fields
    assert "execute" not in SuggestionCandidate.model_fields
    with pytest.raises(ValueError):
        SuggestionCandidate.model_validate_json(
            candidate.model_copy(update={"priority": "low"}).model_dump_json()
        )


@pytest.mark.parametrize(
    "tool,field,value",
    [
        ("get_stockout_risk", "probability", 0.799),
        ("get_stockout_risk", "threshold", 0.95),
        ("get_stockout_risk", "inventory_source_ref", "fixture-other-inventory"),
        ("get_stockout_risk", "inventory_as_of", "2026-08-22T23:59:58Z"),
        ("get_model_status", "deployed_release_ref", "fixture-other-model-release"),
        ("get_model_status", "model_id", "model-sha256-" + "b" * 64),
    ],
)
def test_incomplete_or_mismatched_lineage_never_authorizes_review(tool, field, value):
    query, cases = qualified(**{tool: lambda raw: raw["items"][0].update({field: value})})
    runner, bearer, query = harness(query, cases=cases)
    result = invoke(runner, bearer, query)
    assert result.status == "succeeded" and result.suggestions == []
    assert result.answer.recommended_actions == []


@pytest.mark.parametrize("change", ["quality", "future", "missing", "duplicate", "old_origin"])
def test_forecast_must_be_current_complete_unique_and_quality_passed(change):
    def mutate(raw):
        rows = raw["result"]["items"]
        if change == "quality":
            rows[0]["quality_status"] = "warning"
        elif change == "future":
            rows[0]["generated_at"] = "2026-08-23T00:01:00Z"
        elif change == "missing":
            pass
        elif change == "duplicate":
            duplicate = json.loads(json.dumps(rows[0]))
            duplicate["release_id"] = "synthetic-other-release"
            rows.append(duplicate)
        else:
            rows[0]["key"].update(forecast_origin="2026-08-21T23:59:59Z", horizon_days=4)

    if change == "old_origin":
        with pytest.raises(ValueError):
            qualified(get_demand_forecast=mutate)
        return
    query, cases = qualified(
        horizon_end="2026-08-26" if change == "missing" else "2026-08-25",
        get_demand_forecast=mutate,
    )
    runner, bearer, query = harness(query, cases=cases)
    result = invoke(runner, bearer, query)
    assert result.suggestions == []
    assert result.answer is None or result.answer.recommended_actions == []


def test_expired_snapshot_does_not_create_a_current_review_candidate():
    query, cases = qualified()
    runner, bearer, query = harness(query, cases=cases)
    session = runner.executor.open_session(bearer, default_scope=query.scope)
    import asyncio

    for call, _ in cases:
        asyncio.run(session.execute_json(call.model_dump_json()))
    from retailops_ai.agent.chat_context import EvidenceSnapshot

    runner.executor.clock = lambda: datetime(2026, 8, 23, 0, 5, tzinfo=UTC)
    snapshot = EvidenceSnapshot.build(session.accepted_outputs(), runner.config.chat)
    catalogue = EvidencePolicy(query, runner.config.config.policy).build(session, snapshot)
    assert catalogue.candidates_json == ()


def test_policy_changes_change_config_and_candidate_identity():
    query, cases = qualified()
    first, token, _ = harness(query, cases=cases)
    second, other, _ = harness(
        query,
        cases=cases,
        resolved=settings(policy={"suggestions": {"minimum_stockout_probability": 0.85}}),
    )
    a = invoke(first, token, query).suggestions[0]
    b = invoke(second, other, query).suggestions[0]
    assert first.config.config_id != second.config.config_id
    assert a.candidate_id != b.candidate_id and a.policy_sha256 != b.policy_sha256


@pytest.mark.parametrize("change", ["action", "priority", "rationale", "refs", "omit", "duplicate"])
def test_model_cannot_edit_or_omit_a_server_candidate(change):
    def transform(req, body):
        if req.expected_kind != "answer":
            return body
        if change == "omit":
            body["recommended_actions"] = []
        elif change == "duplicate":
            body["recommended_actions"].append(body["recommended_actions"][0].copy())
        else:
            field = "evidence_refs" if change == "refs" else change
            body["recommended_actions"][0][field] = (
                [body["evidence"][0]["source_ref"]]
                if change == "refs"
                else "Order 999 units"
                if change == "action"
                else "low"
                if change == "priority"
                else "The model found the cause."
            )
        return body

    query, cases = qualified()
    runner, bearer, query = harness(query, cases=cases, provider_options={"transform": transform})
    result = invoke(runner, bearer, query)
    assert result.error_code == "invalid_evidence" and result.suggestions == []
    assert result.trace.repairs == 1


def test_anomaly_actions_require_the_selected_grain_not_only_the_tool_reference():
    # A current tool response may contain several products, but claims for p-101
    # cannot support a p-102 review even when both rows share one result hash.
    from retailops_ai.agent.evidence import Catalogue, Fact

    runner, token, query = harness(request("anomalies"))
    result = invoke(runner, token, query)
    candidate = result.suggestions[0]
    foreign = candidate.model_dump(mode="json") | {"product_id": "p-102"}
    foreign["candidate_id"] = "candidate-sha256-" + canonical_sha256(
        {key: value for key, value in foreign.items() if key != "candidate_id"}
    )
    claim = result.answer.evidence[0]
    catalogue = Catalogue(
        (Fact("anomaly", "8/10", claim.model_dump_json(), ()),),
        "answered",
        (),
        result.answer.data_freshness.model_dump_json(),
        (),
        False,
        (json.dumps(foreign),),
    )
    assert catalogue.suggestions(catalogue.facts) == ()


def test_recommendation_intent_is_closed_and_requires_future_horizon():
    with pytest.raises(ValueError):
        GraphRequest.model_validate_json(
            request("sales").model_copy(update={"intent": "recommendations"}).model_dump_json()
        )
    assert AnswerDraft.model_fields["recommended_actions"].is_required()
