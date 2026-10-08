"""Bounded causal sales, including verified tiny Source fixtures; no LLM quality claims."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_agent_graph import harness
from test_agent_graph import request as graph_request
from test_agent_tools import NOW, authority, policy
from test_day_qualification import prepared as prepared

from retailops_ai.adapters.qualified_sales_tool import QualifiedSalesReader, QualifiedSalesTool
from retailops_ai.agent.execution import ToolExecutor, ToolFailure
from retailops_ai.agent.tools import OUTPUT, QualifiedSalesEvidence, SalesRequest, SalesResult
from retailops_ai.domain.access import Principal

PRODUCT = "22222222-2222-4222-8222-222222222222"
STORE = "33333333-3333-4333-8333-333333333333"


def request():
    return SalesRequest.model_validate_json(
        json.dumps(
            dict(
                schema_version="1.0",
                contract_type="tool_request",
                tool="get_sales_summary",
                scope=dict(product_ids=[PRODUCT], selling_location_ids=[STORE], channel="store"),
                as_of=(NOW - timedelta(seconds=1)).isoformat(),
                limit=5,
                window=dict(start="2026-08-21", end="2026-08-22"),
                grain="product_selling_location_channel_period",
            )
        )
    )


def result(value=None, statuses=("qualified", "qualified"), *, zero=False):
    value = value or request()
    points = []
    for i, state in enumerate(statuses):
        point = dict(
            event_type="sale_completed",
            business_date=(value.window.start + timedelta(days=i)).isoformat(),
            product_id=value.scope.product_ids[0],
            selling_location_id=value.scope.selling_location_ids[0],
            channel=value.scope.channel,
            currency="PLN",
            as_of=value.as_of.isoformat(),
            status=state,
        )
        if state == "qualified":
            point.update(
                score_eligible=True,
                raw_dq_completeness="qualified",
                observed_units=0 if zero else i + 2,
                amount="0.00",
                rejected_units=0,
            )
        points.append(point)
    evidence = QualifiedSalesEvidence.model_validate_json(
        json.dumps(
            dict(
                environment="test",
                request=value.model_dump(mode="json"),
                source_dataset_id="source-sha256-" + "a" * 64,
                curated_dataset_id="curated-sha256-" + "b" * 64,
                full_dq_replay_id="full-dq-replay-sha256-" + "c" * 64,
                full_dq_descriptor_sha256="c" * 64,
                day_coverage_id="day-coverage-sha256-" + "e" * 64,
                day_coverage_descriptor_sha256="e" * 64,
                qualification_runtime_sha256="1" * 64,
                points=points,
            )
        )
    )
    return SalesResult(
        schema_version="1.0",
        contract_type="agent_tool_result",
        tool="get_sales_summary",
        source_kind="runtime",
        status="ok" if evidence.complete else "no_data",
        as_of=value.as_of,
        freshness_status="current" if evidence.complete else "missing",
        source_ref=evidence.view_ref,
        items=evidence.sales_items(),
        error=None,
        qualified_days=evidence,
    )


def actor(value=None):
    value = value or request()
    return Principal(
        "sales-operator",
        frozenset({"operator"}),
        frozenset({"assistant:query", "sales:read"}),
        frozenset(value.scope.product_ids),
        frozenset(value.scope.selling_location_ids),
        frozenset({value.scope.channel}),
    )


class Reader:
    environment = "test"

    def __init__(self, output=None, error=None):
        self.output = output or result()
        self.error = error
        self.calls = []

    def read(self, value, principal):
        self.calls.append((value, principal))
        if self.error:
            raise self.error
        return self.output


def test_qualified_sum_retains_exact_daily_evidence_and_parents():
    value = result()
    assert value.items[0].observed_sales_units == 5
    assert [p.observed_units for p in value.qualified_days.points] == [2, 3]
    assert OUTPUT.validate_json(value.model_dump_json()) == value
    schema = json.loads(
        (
            Path(__file__).parents[1] / "contracts/agent/v1/qualified-sales-evidence.v1.schema.json"
        ).read_bytes()
    )
    assert schema["properties"] == QualifiedSalesEvidence.model_json_schema()["properties"]


@pytest.mark.parametrize(
    "state",
    [
        "no_declaration",
        "closure_unavailable",
        "source_incomplete",
        "location_closed",
        "dq_unattributed_quarantine",
        "dq_missing_facts",
    ],
)
def test_any_unknown_day_withholds_entire_period_not_partial_or_zero(state):
    value = result(statuses=("qualified", state))
    assert value.status == "no_data" and value.items == []
    assert value.qualified_days.points[0].observed_units == 2
    assert value.qualified_days.points[1].observed_units is None
    assert value.source_ref == value.qualified_days.view_ref


def test_explicit_zero_requires_all_days_qualified():
    value = result(zero=True)
    assert value.status == "ok" and value.items[0].observed_sales_units == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "product",
        "location",
        "channel",
        "day",
        "event",
        "cutoff",
        "currency",
        "limit",
        "precision",
    ],
)
def test_rejects_changed_partial_or_noncausal_daily_evidence(mutation):
    raw = result().qualified_days.model_dump(mode="json")
    point = raw["points"][0]
    if mutation == "missing":
        raw["points"].pop()
    elif mutation == "duplicate":
        raw["points"][1] = deepcopy(point)
    elif mutation == "product":
        point["product_id"] = STORE
    elif mutation == "location":
        point["selling_location_id"] = PRODUCT
    elif mutation == "channel":
        point["channel"] = "online"
    elif mutation == "day":
        point["business_date"] = "2026-08-20"
    elif mutation == "event":
        point["event_type"] = "return_completed"
    elif mutation == "cutoff":
        point["as_of"] = NOW.isoformat()
    elif mutation == "currency":
        point["currency"] = "EUR"
    elif mutation == "limit":
        raw["request"]["scope"]["product_ids"].append(STORE)
        raw["request"]["limit"] = 1
    elif mutation == "precision":
        point["observed_units"] = 2**53
    with pytest.raises(ValidationError):
        QualifiedSalesEvidence.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "field", ["items", "source_ref", "as_of", "status", "source_kind", "freshness_status"]
)
def test_result_cannot_claim_different_sum_source_time_or_outcome(field):
    raw = result().model_dump(mode="json")
    if field == "items":
        raw[field][0]["observed_sales_units"] += 1
    elif field == "source_ref":
        raw[field] = "sales-view-sha256-" + "a" * 64
    elif field == "as_of":
        raw[field] = NOW.isoformat()
    elif field == "status":
        raw[field] = "no_data"
    elif field == "source_kind":
        raw[field] = "fixture"
        raw["source_ref"] = "fixture-sales"
    elif field == "freshness_status":
        raw[field] = "stale"
    with pytest.raises(ValidationError):
        OUTPUT.validate_json(json.dumps(raw))


def test_adapter_narrows_same_identity_before_read_without_mutating_grant():
    broad = replace(
        actor(),
        product_ids=frozenset({PRODUCT, "outside-product"}),
        selling_location_ids=frozenset({STORE, "outside-location"}),
        channels=frozenset({"store", "online"}),
    )
    reader = Reader()
    output = asyncio.run(QualifiedSalesTool(reader, "test").execute(request(), broad, None))
    assert output == result()
    assert reader.calls == [(request(), actor())]
    assert broad.product_ids == frozenset({PRODUCT, "outside-product"})


@pytest.mark.parametrize(
    "restriction", ["role", "assistant", "sales", "product", "location", "channel"]
)
def test_unauthorized_scope_never_reaches_reader(restriction):
    principal = actor()
    if restriction == "role":
        principal = replace(principal, roles=frozenset({"viewer"}))
    elif restriction in {"assistant", "sales"}:
        principal = replace(
            principal,
            capabilities=principal.capabilities
            - {restriction + ":query" if restriction == "assistant" else "sales:read"},
        )
    elif restriction == "product":
        principal = replace(principal, product_ids=frozenset({STORE}))
    elif restriction == "location":
        principal = replace(principal, selling_location_ids=frozenset({PRODUCT}))
    else:
        principal = replace(principal, channels=frozenset({"online"}))
    reader = Reader()
    with pytest.raises(ToolFailure) as error:
        asyncio.run(QualifiedSalesTool(reader, "test").execute(request(), principal, None))
    assert error.value.code == "unauthorized" and reader.calls == []


def test_full_series_and_day_budget_is_checked_before_read():
    raw = request().model_dump(mode="json")
    raw["scope"]["product_ids"] = [PRODUCT, STORE, "44444444-4444-4444-8444-444444444444"]
    raw["window"]["start"] = "2026-06-01"
    value = SalesRequest.model_validate_json(json.dumps(raw))
    reader = Reader()
    with pytest.raises(ToolFailure) as error:
        asyncio.run(QualifiedSalesTool(reader, "test").execute(value, actor(value), None))
    assert error.value.code == "budget_exceeded" and reader.calls == []


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("private-upstream-marker"), ToolFailure("not_found"), asyncio.CancelledError()],
)
def test_adapter_hides_upstream_errors_and_preserves_cancellation(failure):
    reader = Reader(error=failure)
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(QualifiedSalesTool(reader, "test").execute(request(), actor(), None))
    else:
        with pytest.raises(ToolFailure) as error:
            asyncio.run(QualifiedSalesTool(reader, "test").execute(request(), actor(), None))
        assert error.value.code == (
            "not_found" if isinstance(failure, ToolFailure) else "unavailable"
        )
        assert "private-upstream-marker" not in str(error.value)


def test_reader_environment_drift_fails_before_read():
    reader = Reader()
    adapter = QualifiedSalesTool(reader, "test")
    reader.environment = "local"
    with pytest.raises(ToolFailure) as error:
        asyncio.run(adapter.execute(request(), actor(), None))
    assert error.value.code == "unavailable" and reader.calls == []


def executor(tmp_path, output):
    access, bearer = authority(capabilities=["assistant:query", "sales:read"])
    raw = access._policy.model_dump(mode="json")
    raw["grants"][0]["scope"].update(product_ids=[PRODUCT], selling_location_ids=[STORE])
    from retailops_ai.security.local import LocalAccess
    from retailops_ai.security.models import AccessPolicy

    access = LocalAccess(AccessPolicy.model_validate_json(json.dumps(raw)))
    return ToolExecutor(
        access,
        {"get_sales_summary": QualifiedSalesTool(Reader(output), "test")},
        policy(),
        "test",
        clock=lambda: NOW,
    ), bearer


def test_executor_requires_qualified_bound_evidence_even_for_runtime_claim(tmp_path):
    raw = result().model_dump(mode="json")
    raw["qualified_days"] = None
    run, bearer = executor(tmp_path, SalesResult.model_validate_json(json.dumps(raw)))
    session = run.open_session(bearer)
    with pytest.raises(ToolFailure) as error:
        asyncio.run(session.execute_json(request().model_dump_json()))
    assert error.value.code == "unavailable" and not session.accepted_outputs()


@pytest.mark.parametrize("empty,zero", [(False, False), (True, False), (False, True)])
def test_graph_keeps_qualified_sales_snapshot_and_distinguishes_zero_from_unknown(
    tmp_path, empty, zero
):
    query = graph_request(
        scope=request().scope.model_dump(mode="json"),
        as_of=request().as_of.isoformat(),
        window=request().window.model_dump(mode="json"),
    )
    value = result(
        statuses=("qualified", "source_incomplete") if empty else ("qualified", "qualified"),
        zero=zero,
    )
    run, bearer = executor(tmp_path, value)
    runner, _, _ = harness(query, access=(run.authority, bearer), cases=(), adapters=run.adapters)
    answer = asyncio.run(runner.run_json(bearer, query.model_dump_json()))
    assert answer.answer.outcome == ("insufficient_evidence" if empty else "answered")
    if empty:
        assert answer.answer.evidence == []
        assert any("source_incomplete" in s for s in answer.answer.limitations)
    else:
        assert str(0 if zero else 5) in answer.answer.evidence[0].claim
    assert len(run.adapters["get_sales_summary"].reader.calls) == 1


@pytest.fixture(scope="module")
def verified_reader(prepared):
    return QualifiedSalesReader(*prepared["args"], "test")


def verified_request(prepared, day):
    raw = request().model_dump(mode="json")
    raw["scope"] = dict(
        product_ids=[day.product_id],
        selling_location_ids=[day.selling_location_id],
        channel=day.channel,
    )
    raw["window"] = dict(start=day.business_date, end=day.business_date)
    return SalesRequest.model_validate_json(json.dumps(raw))


def test_verified_source_fixture_sales_matches_independent_receipts_and_survives_rebind(
    prepared, verified_reader
):
    day = next(
        d
        for d in prepared["days"]
        if d.event_type == "sale_completed"
        and d.expected_business_ids
        and prepared["gate"]
        .point(
            tuple(
                getattr(d, k)
                for k in (
                    "event_type",
                    "business_date",
                    "product_id",
                    "selling_location_id",
                    "channel",
                    "currency",
                )
            ),
            request().as_of.isoformat(),
        )
        .status
        == "qualified"
    )
    value = verified_request(prepared, day)
    output = verified_reader.read(value, actor(value))
    total = sum(
        f["quantity"]
        for f in prepared["replay"]["accepted_facts"]
        if f["event_type"] == "sale_completed" and f["business_id"] in day.expected_business_ids
    )
    assert output.items[0].observed_sales_units == total > 0
    assert output.qualified_days.full_dq_replay_id == prepared["full"].full_dq_replay_id
    assert output.qualified_days.day_coverage_id == prepared["coverage"].coverage_id
    assert QualifiedSalesReader(*prepared["args"], "test").read(value, actor(value)) == output


@pytest.mark.parametrize("kind", ["zero", "closed", "missing", "future_closure"])
def test_verified_source_fixture_keeps_causal_day_semantics(prepared, verified_reader, kind):
    days = [d for d in prepared["days"] if d.event_type == "sale_completed"]
    if kind == "zero":
        day = next(
            d
            for d in days
            if d.activity == "open"
            and d.source_complete
            and not d.expected_business_ids
            and prepared["gate"]
            .point(
                tuple(
                    getattr(d, k)
                    for k in (
                        "event_type",
                        "business_date",
                        "product_id",
                        "selling_location_id",
                        "channel",
                        "currency",
                    )
                ),
                request().as_of.isoformat(),
            )
            .status
            == "qualified"
        )
    elif kind == "closed":
        day = next(d for d in days if d.activity == "closed")
    else:
        day = next(d for d in days if d.expected_business_ids and d.activity == "open")
    value = verified_request(prepared, day)
    if kind == "missing":
        raw = value.model_dump(mode="json")
        raw["window"] = dict(start="2026-07-01", end="2026-07-01")
        value = SalesRequest.model_validate_json(json.dumps(raw))
    elif kind == "future_closure":
        from retailops_ai.raw_dq.contract import stamp

        raw = value.model_dump(mode="json")
        raw["as_of"] = (stamp(day.known_at) - timedelta(microseconds=1)).isoformat()
        value = SalesRequest.model_validate_json(json.dumps(raw))
    output = verified_reader.read(value, actor(value))
    if kind == "zero":
        assert output.status == "ok" and output.items[0].observed_sales_units == 0
    else:
        assert output.status == "no_data" and output.items == []
        assert (
            output.qualified_days.points[0].status
            == {
                "closed": "location_closed",
                "missing": "no_declaration",
                "future_closure": "closure_unavailable",
            }[kind]
        )


@pytest.mark.parametrize("mode", ["known", "closed"])
def test_verified_source_fixture_reaches_http_planner_graph_and_stored_trace(
    prepared, verified_reader, tmp_path, mode
):
    from datetime import UTC, datetime

    from test_assistant import CaptureStore, client, headers, setup

    from retailops_ai.agent.graph_config import load_graph_config
    from retailops_ai.assistant.routes import load_question_routes, reviewed_backend
    from retailops_ai.assistant.source_catalog import load_source_catalog
    from retailops_ai.security.local import LocalAccess
    from retailops_ai.security.models import AccessPolicy

    day = next(
        d
        for d in prepared["days"]
        if d.event_type == "sale_completed"
        and d.channel == "store"
        and (
            d.activity == "closed"
            if mode == "closed"
            else d.expected_business_ids
            and prepared["gate"]
            .point(
                tuple(
                    getattr(d, k)
                    for k in (
                        "event_type",
                        "business_date",
                        "product_id",
                        "selling_location_id",
                        "channel",
                        "currency",
                    )
                ),
                request().as_of.isoformat(),
            )
            .status
            == "qualified"
        )
    )
    path, tokens, _, body, _, _ = setup(tmp_path)
    raw = json.loads(path.read_bytes())
    for grant in raw["grants"]:
        if grant["scope"] is not None:
            grant["scope"].update(
                product_ids=[day.product_id], selling_location_ids=[day.selling_location_id]
            )
    path.write_text(json.dumps(raw))
    auth = LocalAccess(AccessPolicy.model_validate_json(json.dumps(raw)))
    now = datetime.now(UTC).replace(microsecond=0)
    root = Path(__file__).parents[1]
    graph = load_graph_config(root / "agent/graph.evaluate.fake.prepaid.v3.json")
    routes = load_question_routes(root / "agent/question-routes.prepaid.proposed.v3.json")
    body["question"] = next(row.question for row in routes.routes if row.intent == "sales")
    body["scope"].update(
        product_ids=[day.product_id],
        store_ids=[day.selling_location_id],
        **{"from": day.business_date, "to": day.business_date},
    )

    def runner():
        instance, _, _ = harness(
            resolved=graph,
            cases=(),
            access=(auth, headers(tokens)["Authorization"]),
            adapters={"get_sales_summary": QualifiedSalesTool(verified_reader, "test")},
        )
        instance.executor.clock = lambda: now
        return instance

    backend = reviewed_backend(
        routes,
        graph,
        load_source_catalog(prepared["args"][3]),
        "store",
        frozenset({"get_sales_summary"}),
        "test",
        "fixture",
        runner,
        allow_proposed=True,
        clock=lambda: now,
    )
    store = CaptureStore()
    with client(path, backend, store) as http:
        response = http.post("/api/v1/assistant/queries", json=body, headers=headers(tokens))
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["outcome"] == ("insufficient_evidence" if mode == "closed" else "answered")
        assert store.admissions == 1 and not store.suggestions
        run = http.get("/api/v1/assistant/runs/" + answer["trace_id"], headers=headers(tokens))
        assert run.status_code == 200 and run.json()["status"] == "succeeded"
        assert run.json()["tools"][0]["source_refs"][0].startswith("sales-view-sha256-")
        if mode == "known":
            from retailops_ai.agent.chat_context import tool_result_ref

            raw_request = verified_request(prepared, day).model_dump(mode="json")
            raw_request["as_of"] = now.isoformat()
            expected_request = SalesRequest.model_validate_json(json.dumps(raw_request))
            expected = verified_reader.read(expected_request, actor(expected_request))
            assert answer["evidence"][0]["source_ref"] == tool_result_ref(expected)
            assert run.json()["tools"][0]["source_refs"] == [expected.source_ref]
        else:
            assert answer["evidence"] == []
            assert any("location_closed" in value for value in answer["limitations"])


@pytest.mark.parametrize(
    "count, days, max_rows, expected_limit", [(6, 7, 50, 6), (3, 7, 2, None), (10, 21, 50, None)]
)
def test_sales_planner_covers_all_series_or_rejects_before_admission(
    count, days, max_rows, expected_limit
):
    from uuid import NAMESPACE_DNS, uuid5

    from test_assistant_routes import GRAPH, PROFILE, catalog, principal, query

    from retailops_ai.agent.graph_config import AgentGraphConfig, resolve_graph_config
    from retailops_ai.assistant.contracts import AssistantQuery
    from retailops_ai.assistant.routes import QuestionRoutes, ReviewedPlanner
    from retailops_ai.assistant.service import AssistantError
    from retailops_ai.assistant.source_catalog import CatalogProduct

    products = [
        str(uuid5(NAMESPACE_DNS, "qualified-sales-planner-" + str(i))) for i in range(count)
    ]
    source = catalog().model_copy(
        update={
            "products": tuple(
                CatalogProduct(product_id=p, available_at=NOW - timedelta(days=100))
                for p in products
            )
        }
    )
    identity = replace(principal(), product_ids=frozenset(products))
    raw = GRAPH.config.model_dump(mode="json")
    raw["chat"]["tool_policy"]["max_rows"] = max_rows
    graph = resolve_graph_config(AgentGraphConfig.model_validate_json(json.dumps(raw)))
    profile = QuestionRoutes.model_validate_json(
        json.dumps(PROFILE.model_dump(mode="json") | {"graph_config_id": graph.config_id})
    )
    planner = ReviewedPlanner(
        profile,
        graph,
        source,
        "store",
        frozenset({"get_sales_summary"}),
        "test",
        allow_proposed=True,
        clock=lambda: NOW,
    )
    raw = query().model_dump(mode="json", by_alias=True)
    raw["scope"].update(
        product_ids=products,
        **{
            "from": (NOW.date() - timedelta(days=days)).isoformat(),
            "to": (NOW.date() - timedelta(days=1)).isoformat(),
        },
    )
    value = AssistantQuery.model_validate_json(json.dumps(raw))
    if expected_limit is None:
        with pytest.raises(AssistantError) as error:
            asyncio.run(planner.prepare(value, identity))
        assert error.value.status == 422
    else:
        assert asyncio.run(planner.prepare(value, identity)).limit == expected_limit
