"""Native adapter invariants on explicit small read-page fixtures, not runtime quality claims."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_agent_graph import harness
from test_agent_graph import request as graph_request
from test_agent_suggestions import qualified
from test_agent_tools import NOW, authority, policy
from test_assistant import CaptureStore, client, headers, setup
from test_assistant_routes import ROOT, catalog

from retailops_ai.adapters.agent_tools import FixtureTools
from retailops_ai.adapters.forecast_v12_tool import NativeForecastTool
from retailops_ai.agent.evidence import required_calls
from retailops_ai.agent.execution import ToolExecutor, ToolFailure
from retailops_ai.agent.graph_config import load_graph_config
from retailops_ai.agent.native_forecast import NativeForecastRead
from retailops_ai.agent.tools import OUTPUT
from retailops_ai.assistant.contracts import AssistantQuery
from retailops_ai.assistant.routes import QuestionRoutes, ReviewedPlanner, reviewed_backend
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.tool import ToolRequest
from retailops_ai.forecast_jobs.read_contracts import ForecastFreshness, Pagination
from retailops_ai.forecast_jobs.reader import ForecastReadError
from retailops_ai.forecast_jobs.v12_read_contracts import V12ForecastItem, V12ForecastPage
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import MODEL


def request():
    return ToolRequest.model_validate_json(
        json.dumps(
            {
                "schema_version": "1.0",
                "contract_type": "tool_request",
                "tool": "get_demand_forecast",
                "scope": {
                    "product_ids": ["p-101"],
                    "selling_location_ids": ["s-03"],
                    "channel": "store",
                    "target_from": "2026-08-23",
                    "target_to": "2026-08-24",
                },
                "as_of": "2026-08-22T23:59:59Z",
                "limit": 5,
            }
        )
    )


def page(value=None, *, empty=False, closed=False, at=NOW):
    value = value or request()
    freshness = ForecastFreshness(
        status="current",
        reason="within_policy",
        source_watermark=value.as_of,
        source_watermark_as_of=value.as_of,
        source_watermark_policy_version="fixture-closed-daily-source-v1",
        source_completeness_status="complete",
        source_watermark_age_seconds=(at - value.as_of).total_seconds(),
        source_watermark_origin_lag_seconds=0,
        latest_complete_observation_date=value.as_of.date(),
        observation_lag_days=0,
        evaluated_at=at,
        origin_age_seconds=(at - value.as_of).total_seconds(),
    )
    rows = []
    for offset in range((value.scope.target_to - value.scope.target_from).days + 1):
        forecast = {
            "mean": None if closed else 10.25 + offset,
            "median": None if closed else 10,
            "interval": None if closed else {"lower": 8, "upper": 12},
        }
        prediction = {
            "key": "explicit-fixture-key-" + str(offset),
            "candidate": forecast,
            "baseline": dict(forecast, mean=None if closed else 9.0),
            "metadata": {
                "selected": None if closed else "fixture-reference",
                "baseline": None if closed else "fixture-reference",
                "mean_source": "fixture-native-mean",
                "exact_reference_median": True,
                "exact_reference_interval": True,
                "recipe_id": "functional-v12-recipe-sha256-" + "a" * 64,
            },
        }
        if closed:
            prediction["exclusion_reason"] = "closed_target"
        rows.append(
            V12ForecastItem(
                product_id=value.scope.product_ids[0],
                selling_location_id=value.scope.selling_location_ids[0],
                channel=value.scope.channel,
                forecast_origin=value.as_of,
                business_timezone="UTC",
                cutoff_policy="end_of_day_second_v1",
                target_date=value.scope.target_from + timedelta(days=offset),
                horizon_days=(value.scope.target_from - value.as_of.date()).days + offset,
                prediction=prediction,
                execution_profile_id="batch-profile-sha256-" + "a" * 64,
                prediction_id="prediction-sha256-" + canonical_sha256(prediction),
                prediction_dataset_id="v12-forecasts-sha256-" + "a" * 64,
                model_name=MODEL,
                model_version="1",
                approval_sha256="a" * 64,
                runtime_pin_sha256="b" * 64,
                image_digest="sha256:" + "c" * 64,
                release_id="v12-model-release-sha256-" + "d" * 64,
                receipt_id="v12-computation-sha256-" + "e" * 64,
                source_dataset_id="source-sha256-" + "f" * 64,
                curated_dataset_id="curated-sha256-" + "a" * 64,
                feature_set_id="features-sha256-" + "b" * 64,
                inference_run_id="run-" + "a" * 32,
                profile_id="batch-profile-sha256-" + "c" * 64,
                generated_at=value.as_of,
                approval_valid_until=at + timedelta(days=7),
                freshness=freshness,
            )
        )
    return V12ForecastPage(
        items=() if empty else tuple(rows),
        pagination=Pagination(
            limit=value.limit, offset=0, total=0 if empty else len(rows), next_offset=None
        ),
        generated_at=at,
        data_status="no_data" if empty else "available",
        selection="origin",
        view_sha256="f" * 64,
    )


class Reader:
    environment = "test"
    model = MODEL

    def __init__(self, value=None, error=None):
        self.value = value or page()
        self.error = error
        self.calls = []

    def read(self, query, actor):
        self.calls.append((query, actor))
        if self.error:
            raise self.error
        return V12ForecastPage.model_validate_json(self.value.model_dump_json())


def execution(reader=None, *, clock=NOW):
    auth, bearer = authority()
    reader = reader or Reader()
    executor = ToolExecutor(
        auth,
        {"get_demand_forecast": NativeForecastTool(reader, "test")},
        policy(),
        "test",
        clock=lambda: clock,
    )
    return executor.open_session(bearer), reader


def invoke(session, value=None):
    return asyncio.run(session.execute_json((value or request()).model_dump_json()))


def test_lossless_native_page_and_daily_freshness_pass_through_executor():
    # A daily forecast can be 12 hours old while its checked read view is new.
    now = NOW + timedelta(hours=12)
    run, reader = execution(Reader(page(at=now)))
    run.executor.clock = lambda: now
    output = invoke(run)
    assert isinstance(output.result, NativeForecastRead)
    assert output.result.page == reader.value
    assert OUTPUT.validate_json(output.model_dump_json()) == output
    assert run.audit[0].source_refs == ("forecast-view-sha256-" + "f" * 64,)
    assert run.accepted_calls()[0][1] == output
    assert "ModelRecord" not in output.model_dump_json()
    assert output.result.items[0].prediction.candidate.mean == 10.25
    assert output.result.items[0].prediction.baseline.mean == 9.0


def test_adapter_narrows_reader_identity_to_exact_requested_scope_without_mutating_actor():
    auth, bearer = authority()
    actor = auth.authenticate(bearer, now=NOW)
    actor = replace(
        actor,
        product_ids=frozenset({"p-101", "other-product"}),
        selling_location_ids=frozenset({"s-03", "other-store"}),
        channels=frozenset({"store", "online"}),
    )
    reader = Reader()
    asyncio.run(NativeForecastTool(reader, "test").execute(request(), actor, None))
    query, narrowed = reader.calls[0]
    assert narrowed.principal_id == actor.principal_id
    assert narrowed.roles == actor.roles and narrowed.capabilities == actor.capabilities
    assert narrowed.product_ids == {"p-101"} and narrowed.selling_location_ids == {"s-03"}
    assert narrowed.channels == {"store"} and actor.channels == {"store", "online"}
    assert query.as_of == request().as_of and query.offset == 0 and query.limit == 5
    assert query.target_from == request().scope.target_from
    assert query.target_to == request().scope.target_to


@pytest.mark.parametrize("change", ["viewer", "capability", "product", "store", "channel"])
def test_adapter_refuses_unauthorized_identity_before_reader(change):
    auth, bearer = authority()
    actor = auth.authenticate(bearer, now=NOW)
    changes = {
        "viewer": {"roles": frozenset({"viewer"})},
        "capability": {"capabilities": actor.capabilities - {"forecast:read"}},
        "product": {"product_ids": frozenset({"foreign"})},
        "store": {"selling_location_ids": frozenset({"foreign"})},
        "channel": {"channels": frozenset({"online"})},
    }
    reader = Reader()
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(
            NativeForecastTool(reader, "test").execute(
                request(), replace(actor, **changes[change]), None
            )
        )
    assert caught.value.code == "unauthorized" and not reader.calls


def test_incomplete_page_budget_is_rejected_before_any_reader_work():
    run, reader = execution()
    with pytest.raises(ToolFailure) as caught:
        invoke(run, request().model_copy(update={"limit": 1}))
    assert caught.value.code == "budget_exceeded" and not reader.calls


@pytest.mark.parametrize(
    "field,value", [("environment", "local"), ("model", MODEL + "-development")]
)
def test_reader_environment_and_namespace_are_bound_at_construction_and_execution(field, value):
    reader = Reader()
    tool = NativeForecastTool(reader, "test")
    setattr(reader, field, value)
    with pytest.raises(ValueError, match="native_forecast_environment_invalid"):
        NativeForecastTool(reader, "test")
    auth, bearer = authority()
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(tool.execute(request(), auth.authenticate(bearer, now=NOW), None))
    assert caught.value.code == "unavailable" and not reader.calls


@pytest.mark.parametrize(
    "change",
    [
        "scope",
        "channel",
        "origin",
        "target",
        "model",
        "expired",
        "partial",
        "duplicate",
        "prediction_id",
        "offset",
        "next",
        "total",
        "selection",
        "freshness_time",
        "age",
    ],
)
def test_native_read_contract_refuses_unbound_or_partial_pages(change):
    raw = page().model_dump(mode="json")
    row = raw["items"][0]
    if change == "scope":
        row["product_id"] = "foreign"
    elif change == "channel":
        row["channel"] = "online"
    elif change == "origin":
        row["forecast_origin"] = "2026-08-21T23:59:59Z"
    elif change == "target":
        row["target_date"] = "2026-08-25"
    elif change == "model":
        row["model_name"] = "retailops-demand-forecast-v12-mechanics"
    elif change == "expired":
        row["approval_valid_until"] = NOW.isoformat()
    elif change == "partial":
        raw["items"].pop()
        raw["pagination"]["total"] = 1
    elif change == "duplicate":
        raw["items"][1] = deepcopy(row)
    elif change == "prediction_id":
        raw["items"][1]["prediction_id"] = row["prediction_id"]
    elif change == "offset":
        raw["pagination"]["offset"] = 1
    elif change == "next":
        raw["pagination"]["next_offset"] = 2
    elif change == "total":
        raw["pagination"]["total"] = 3
    elif change == "selection":
        raw["selection"] = "latest_per_series_horizon"
    elif change == "freshness_time":
        row["freshness"]["evaluated_at"] = (NOW - timedelta(seconds=1)).isoformat()
    elif change == "age":
        row["freshness"]["origin_age_seconds"] = 0
    with pytest.raises(ValidationError):
        NativeForecastRead.model_validate_json(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "request": request().model_dump(mode="json"),
                    "environment": "test",
                    "page": raw,
                }
            )
        )


@pytest.mark.parametrize("case", ["future", "cached", "stale", "unknown"])
def test_executor_rejects_future_cached_stale_and_unknown_native_evidence(case):
    if case == "future":
        value = page(at=NOW + timedelta(seconds=1))
    elif case == "cached":
        value = page(at=NOW + timedelta(seconds=301))
    else:
        raw = page().model_dump(mode="json")
        for row in raw["items"]:
            row["freshness"].update(
                status="stale" if case == "stale" else "unknown",
                reason="newer_run_unpublished"
                if case == "stale"
                else "source_observation_unavailable",
            )
        value = V12ForecastPage.model_validate_json(json.dumps(raw))
    run, _ = execution(Reader(value))
    if case == "cached":
        run.executor.clock = lambda: NOW + timedelta(seconds=602)
    with pytest.raises(ToolFailure) as caught:
        invoke(run)
    assert caught.value.code == ("unavailable" if case in {"future", "cached"} else "stale")
    assert not run.accepted_outputs()


@pytest.mark.parametrize("case", ["approval_expired", "origin_expired"])
def test_decision_time_rechecks_approval_and_origin_even_for_a_recent_checked_page(case):
    at = NOW if case == "approval_expired" else request().as_of + timedelta(days=1)
    raw = page(at=at).model_dump(mode="json")
    if case == "approval_expired":
        for row in raw["items"]:
            row["approval_valid_until"] = (at + timedelta(seconds=1)).isoformat()
    run, _ = execution(Reader(V12ForecastPage.model_validate_json(json.dumps(raw))))
    run.executor.clock = lambda: at + timedelta(seconds=2)
    with pytest.raises(ToolFailure) as caught:
        invoke(run)
    assert caught.value.code == "stale" and not run.accepted_outputs()


@pytest.mark.parametrize(
    "status,code,expected",
    [
        (403, "forecast-read-denied", "unauthorized"),
        (429, "forecast-read-budget", "budget_exceeded"),
        (503, "forecast-output-invalid", "unavailable"),
    ],
)
def test_native_reader_errors_are_canonical_without_private_details(status, code, expected):
    run, _ = execution(Reader(error=ForecastReadError(status, code)))
    with pytest.raises(ToolFailure) as caught:
        invoke(run)
    assert caught.value.code == expected and code not in str(caught.value)


@pytest.mark.parametrize("empty,closed", [(False, False), (True, False), (False, True)])
def test_native_forecast_reaches_real_graph_with_no_invented_mean_or_suggestions(empty, closed):
    query = graph_request("forecast", window={"start": "2026-08-23", "end": "2026-08-24"})
    reader = Reader(page(empty=empty, closed=closed))
    runner, bearer, _ = harness(
        query, cases=(), adapters={"get_demand_forecast": NativeForecastTool(reader, "test")}
    )
    result = asyncio.run(runner.run_json(bearer, query.model_dump_json()))
    assert result.status == "succeeded", result.model_dump_json()
    assert not result.suggestions
    assert result.answer.outcome == ("insufficient_evidence" if empty or closed else "answered")
    if empty or closed:
        assert not result.answer.evidence and "=0 unit" not in result.answer.summary
    else:
        assert "mean of observed sales=10.25 unit" in result.answer.summary
        assert "quality=passed_at_publication" in result.answer.summary
        assert reader.value.items[0].prediction_id in result.answer.summary
        assert result.trace.release_refs == [reader.value.items[0].release_id]
        assert result.answer.data_freshness.predictions.as_of == request().as_of


def test_native_release_is_not_coerced_into_a_legacy_replenishment_candidate():
    query, cases = qualified()
    call = next(call for call, _ in cases if call.tool == "get_demand_forecast")
    fixtures = FixtureTools(cases)
    adapters = {call.tool: fixtures for call, _ in cases}
    adapters["get_demand_forecast"] = NativeForecastTool(Reader(page(call)), "test")
    runner, bearer, _ = harness(query, cases=cases, adapters=adapters)
    result = asyncio.run(runner.run_json(bearer, query.model_dump_json()))
    assert result.status == "succeeded", result.model_dump_json()
    assert result.trace.tool_calls == 4
    assert not any(row.recommendation_type == "review_replenishment" for row in result.suggestions)


@pytest.mark.parametrize("mode", ["answered", "empty", "oversized"])
def test_native_forecast_http_planning_graph_and_store(tmp_path, mode):
    # Both the read page and chat provider are explicit test doubles. This tests
    # the real HTTP/planner/graph boundaries, not PostgreSQL or Sonnet acceptance.
    path, tokens, auth, body, original, _ = setup(
        tmp_path, capabilities=["assistant:query", "forecast:read"]
    )
    actor = auth.authenticate(headers(tokens)["Authorization"])
    now = asyncio.run(
        original.prepare(AssistantQuery.model_validate_json(json.dumps(body)), actor)
    ).as_of
    graph = load_graph_config(ROOT / "agent/graph.fake.prepaid.v5.json")
    routes = QuestionRoutes(
        schema_version="1.0",
        profile="assistant-question-routes-v1",
        labels_state="proposed",
        graph_config_id=graph.config_id,
        routes=({"question": "What forecast evidence is available?", "intent": "forecast"},),
    )
    body["question"] = routes.routes[0].question
    body["scope"].update(
        {
            "from": (now.date() + timedelta(days=1)).isoformat(),
            "to": (now.date() + timedelta(days=7)).isoformat(),
        }
    )
    source = catalog()
    if mode == "oversized":
        body["scope"]["to"] = (now.date() + timedelta(days=21)).isoformat()
    options = dict(
        profile=routes,
        graph=graph,
        catalog=source,
        channel="store",
        available_tools=frozenset({"get_demand_forecast"}),
        environment="test",
        allow_proposed=True,
        clock=lambda: now,
    )
    reader = Reader()
    if mode != "oversized":
        prepared = asyncio.run(
            ReviewedPlanner(**options).prepare(
                AssistantQuery.model_validate_json(json.dumps(body)), actor
            )
        )
        call = required_calls(prepared)[0]
        reader.value = page(call, empty=mode == "empty", at=now)
        assert prepared.limit == 7
    runners = []

    def runner():
        instance, _, _ = harness(
            cases=(),
            resolved=graph,
            access=(auth, headers(tokens)["Authorization"]),
            adapters={"get_demand_forecast": NativeForecastTool(reader, "test")},
        )
        instance.executor.clock = lambda: now
        runners.append(instance)
        return instance

    backend = reviewed_backend(**options, source_kind="fixture", runner=runner)
    store = CaptureStore()
    with client(path, backend, store) as http:
        response = http.post("/api/v1/assistant/queries", json=body, headers=headers(tokens))
        if mode == "oversized":
            assert response.status_code == 422 and store.admissions == 0
            assert not reader.calls and not runners
            return
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["outcome"] == ("answered" if mode == "answered" else "insufficient_evidence")
        assert answer["agent_config_version"] == backend.config_version
        assert store.admissions == 1 and len(reader.calls) == 1
        assert not store.suggestions
        run = http.get("/api/v1/assistant/runs/" + answer["trace_id"], headers=headers(tokens))
        assert run.status_code == 200 and run.json()["status"] == "succeeded"
        assert runners[0].provider.source_kind == "fixture"
        if mode == "answered":
            assert reader.value.items[0].release_id in answer["summary"]
        else:
            assert not answer["evidence"] and "=0 unit" not in answer["summary"]
