"""Native DTO and read boundary mechanics; synthetic pins are not model acceptance."""

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_agent_graph import harness, invoke
from test_agent_graph import request as graph_request
from test_agent_tools import NOW, policy
from test_native_inventory_tool import PRODUCT, STOCK, STORE, access
from test_native_inventory_tool import actor as inventory_actor
from test_native_inventory_tool import proof as inventory_proof
from test_native_inventory_tool import result as inventory_result

from retailops_ai.adapters.native_anomaly_tool import (
    NativeAnomalyTool,
    PostgresNativeAnomalyReader,
    verify_publication,
)
from retailops_ai.adapters.native_model_status_tool import NativeModelStatusTool
from retailops_ai.adapters.native_operations_tool import (
    NativeOperationsTool,
    PostgresNativeOperationsReader,
)
from retailops_ai.adapters.native_stockout_tool import NativeStockoutTool
from retailops_ai.agent.execution import READ_CAPABILITIES, ToolExecutor, ToolFailure
from retailops_ai.agent.tools import (
    INPUT,
    OUTPUT,
    NativeOperationsEvidence,
)
from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.anomaly_portfolio.lifecycle_contract import Binding, Qualification, release_for
from retailops_ai.anomaly_portfolio.serving_contract import Item, Page, Pagination
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.model_lifecycle.contracts import GATES, Gate, Receipt
from retailops_ai.model_lifecycle.read_contracts import (
    CatalogFreshness,
    CatalogModel,
    CatalogPagination,
    ModelPage,
)
from retailops_ai.model_lifecycle.v12_metadata_contracts import V12CatalogModel, V12ModelPage
from retailops_ai.security.local import LocalAccess
from retailops_ai.security.models import AccessPolicy
from retailops_ai.source_snapshot.files import canonical_json, json_sha256
from retailops_ai.stockout_jobs.read_contracts import StockoutRiskPage

KINDS = {
    "risk": "get_stockout_risk",
    "anomalies": "get_detected_anomalies",
    "operations": "get_live_operations",
    "model": "get_model_status",
}


def request(kind, **updates):
    raw = dict(
        schema_version="1.0",
        contract_type="tool_request",
        tool=KINDS[kind],
        scope=dict(product_ids=[PRODUCT], selling_location_ids=[STORE], channel="store"),
        as_of=NOW.isoformat(),
        limit=5,
    )
    if kind == "risk":
        raw["window"] = dict(start="2026-08-24", end="2026-08-30")
    elif kind == "anomalies":
        raw["window"] = dict(start="2026-08-22", end="2026-08-22")
    raw.update(updates)
    return INPUT.validate_json(json.dumps(raw))


def actor():
    return replace(
        inventory_actor(),
        capabilities=frozenset({"assistant:query", *READ_CAPABILITIES.values(), "anomaly:read"}),
    )


def auth():
    original, token = access()
    raw = original._policy.model_dump(mode="json")
    raw["grants"][0]["capabilities"] = sorted(actor().capabilities - {"knowledge:read"})
    # Preserve the generated credential and scope; none is printed.
    return LocalAccess(AccessPolicy.model_validate_json(json.dumps(raw))), token


def risk_page(state="scored"):
    row = dict(
        risk_id="risk-sha256-" + "1" * 64,
        product_id=PRODUCT,
        stock_location_id=STOCK,
        as_of=NOW.isoformat(),
        status=state,
        status_reason=None
        if state == "scored"
        else "already_stockout"
        if state == "already_stockout"
        else "inventory_unknown",
        probability=0.83 if state == "scored" else None,
        risk_band="high" if state == "scored" else None,
        threshold_version="stockout-scoring-policy-sha256-" + "2" * 64,
        calibrator_version="stockout-calibrator-sha256-" + "3" * 64,
        model_name="retailops-stockout-risk",
        model_version="1",
        release_id="stockout-release-sha256-" + "4" * 64,
        top_factors=[dict(code="known_available_stock", field="available_qty", value=10)],
        inventory_freshness_status="unknown" if state == "insufficient_data" else "current",
        freshness_status="current",
        lineage=dict(
            source_dataset_id="source-sha256-" + "c" * 64,
            curated_dataset_id="curated-sha256-" + "d" * 64,
            feature_set_id="feature-partitions-sha256-" + "5" * 64,
            upstream_bundle_id="upstream-partitions-sha256-" + "6" * 64,
            source_watermark=NOW.isoformat(),
            source_completeness_status="complete",
        ),
        upstream_lineage_sha256=None,
        feature_lineage_sha256="7" * 64,
        inference_run_id="run-" + "0" * 32,
        generated_at=NOW.isoformat(),
        quality_status="passed_at_publication",
        read_at=NOW.isoformat(),
        origin_age_seconds=0.0,
        output_age_seconds=0.0,
        freshness_reason="within_policy",
    )
    return StockoutRiskPage.model_validate_json(
        json.dumps(
            dict(
                items=[row],
                pagination=dict(limit=5, offset=0, total=1, next_offset=None),
                generated_at=NOW.isoformat(),
                data_status="available",
                selection="origin",
                view_sha256="8" * 64,
            )
        )
    )


def anomaly_publication(state="alert", day=None):
    """Synthetic native publication with exact census/hash/release shape, no training."""
    day = day or (NOW - timedelta(days=1)).date()
    receipt = Receipt(sha256="1" * 64, size_bytes=1)
    q = Qualification(
        evidence_id="fixture-mechanics",
        evaluation_id="fixture-evaluation",
        reference_id="fixture-reference",
        source_dataset_id="source-sha256-" + "c" * 64,
        qualified_anomaly_input_id="qualified-anomaly-inputs-sha256-" + "d" * 64,
        original_started_at=NOW - timedelta(days=5),
        original_completed_at=NOW - timedelta(days=4),
        source_code_commit="2" * 40,
        ai_code_commit="3" * 40,
        dependency_lock_sha256="4" * 64,
        model_family="seasonal_residual",
        model_seed=42,
        model=receipt,
        config=receipt,
        signature=receipt,
        input_example=receipt,
        expected_output_sha256="5" * 64,
        gates={k: Gate(status="passed", report=receipt) for k in GATES},
    )
    release = release_for(
        decision_id="decision-anomaly-fixture",
        binding=Binding(
            model_version="1",
            mlflow_run_id="6" * 32,
            source_uri="mlflow-artifacts:/fixture/anomaly",
            qualification_sha256="7" * 64,
            qualification=q,
        ).model_dump(mode="json"),
        image_digest="sha256:" + "8" * 64,
        previous_release_id=None,
        previous_version=None,
        restored_from_release_id=None,
    )
    unknown = state == "insufficient_data"
    decision = Decision(
        product_id=PRODUCT,
        selling_location_id=STORE,
        channel="store",
        currency="PLN",
        event_type="sale_completed",
        business_date=day,
        scoring_origin=NOW,
        detector_id="anomaly-detector-sha256-" + "9" * 64,
        family="seasonal_residual",
        role="batch",
        status="insufficient_data" if unknown else "scored",
        score=None if unknown else 2.0 if state == "alert" else 0.5,
        threshold=None if unknown else 1.0,
        alert=None if unknown else state == "alert",
        severity=None if unknown else "high" if state == "alert" else "none",
        explanation_codes=(),
        observed_units=None if unknown else 90,
        expected_units=None if unknown else 10,
        residual_units=None if unknown else 80,
        promotion_offered=None,
        on_hand=None,
        input_status="day_unqualified" if unknown else "ready_input",
    )
    desc = dict(
        version="anomaly-complete-batch-1.0.0",
        release_id=release.release_id,
        model_version="1",
        model_sha256=receipt.sha256,
        source_dataset_id=q.source_dataset_id,
        feature_id=q.qualified_anomaly_input_id,
        feature_manifest_sha256="a" * 64,
        window=dict(start=day.isoformat(), end=day.isoformat()),
        as_of=NOW.isoformat(),
        scopes=[
            decision.model_dump(
                mode="json",
                include={"event_type", "product_id", "selling_location_id", "channel", "currency"},
            )
        ],
        decisions_sha256=json_sha256([decision.model_dump(mode="json")]),
        row_count=1,
        truth_access="excluded",
        transport_durability="offline_only",
    )
    batch_id = "anomaly-batch-sha256-" + json_sha256(desc)
    item = Item.model_validate_json(
        json.dumps(
            dict(
                **decision.model_dump(mode="json"),
                anomaly_id="anomaly-sha256-"
                + json_sha256(dict(batch_id=batch_id, decision=decision.model_dump(mode="json"))),
                batch_id=batch_id,
                signal_episode_id="signal-episode-sha256-" + "b" * 64 if state == "alert" else None,
                observed_window=desc["window"],
                detected_at=NOW.isoformat(),
                inference_run_id=batch_id,
                inventory_context=dict(stock_location_id=None, on_hand=None, status="unavailable"),
                promotion_context=dict(offered=None, planned_price=None),
                detector_version="1",
                release_id=release.release_id,
                source_dataset_id=q.source_dataset_id,
                curated_dataset_id="curated-sha256-" + "d" * 64,
                qualified_anomaly_input_id=q.qualified_anomaly_input_id,
                full_dq_replay_id="full-dq-replay-sha256-" + "e" * 64,
                generated_at=NOW.isoformat(),
                as_of=NOW.isoformat(),
                freshness_status="unknown" if unknown else "current",
                anomaly_type="sales_spike"
                if state == "alert"
                else "data_quality_suspicion"
                if unknown
                else None,
                alert_status="insufficient_data"
                if unknown
                else "open"
                if state == "alert"
                else "no_alert",
                score_definition="absolute_causal_standardized_residual",
            )
        )
    )
    manifest = dict(
        batch_id=batch_id,
        descriptor=desc,
        rows_sha256=hashlib.sha256(
            canonical_json(item.model_dump(mode="json")) + b"\n"
        ).hexdigest(),
    )
    return manifest, release, [item]


def anomaly_page(state="alert"):
    items = anomaly_publication(state)[2]
    return Page(
        items=tuple(items),
        pagination=Pagination(limit=5, offset=0, total=1),
        generated_at=NOW,
        view_sha256="f" * 64,
        selection="latest_complete_batch",
        data_status="available",
    )


def operations_proof(empty=False):
    row = dict(
        product_id=PRODUCT,
        selling_location_id=STORE,
        channel="store",
        received=0,
        processed=0 if empty else 2,
        failed_dead_lettered=0,
        ignored_duplicate=0,
        newer_state_not_evaluable=0,
        latest_ingested_at=None if empty else (NOW - timedelta(seconds=8)).isoformat(),
        latest_processed_at=None if empty else (NOW - timedelta(seconds=5)).isoformat(),
        max_processing_latency_seconds=None if empty else 3.0,
    )
    return NativeOperationsEvidence.model_validate_json(
        json.dumps(
            dict(
                environment="test",
                request=request("operations").model_dump(mode="json"),
                observed_at=NOW.isoformat(),
                source_view_sha256="1" * 64,
                points=[row],
            )
        )
    )


def model_pages():
    pagination = CatalogPagination(limit=5, offset=0, total=1, next_offset=None)
    common = dict(
        visible_version_count=1, freshness=CatalogFreshness(evaluated_at=NOW), generated_at=NOW
    )
    forecast = V12ModelPage(
        items=(
            V12CatalogModel(
                model_name="retailops-demand-forecast-v12",
                approved_release=dict(
                    release_id="v12-model-release-sha256-" + "1" * 64,
                    model_version="1",
                    image_digest="sha256:" + "2" * 64,
                ),
                **common,
            ),
        ),
        pagination=pagination,
        generated_at=NOW,
        data_status="available",
        view_sha256="3" * 64,
    )
    anomaly = ModelPage(
        items=(
            CatalogModel(
                model_name="retailops-sales-anomaly",
                approved_release=dict(
                    release_id="anomaly-release-sha256-" + "4" * 64,
                    model_version="1",
                    image_digest="sha256:" + "5" * 64,
                ),
                **common,
            ),
        ),
        pagination=pagination,
        generated_at=NOW,
        data_status="available",
        view_sha256="6" * 64,
    )
    return forecast, anomaly


class Reader:
    environment = "test"
    model = "retailops-stockout-risk"
    name = "retailops-demand-forecast-v12"

    def __init__(self, value, error=None):
        self.value, self.error, self.calls = value, error, []

    def take(self, request, actor):
        self.calls.append((request, actor))
        if self.error:
            raise self.error
        return self.value

    read = take
    list = take

    def models(self, query, actor):
        value = self.take(query, actor)
        rows = [
            r.model_dump(mode="json", exclude={"freshness", "generated_at"}) for r in value.items
        ]
        if isinstance(value, V12ModelPage):
            digest = canonical_sha256(
                dict(
                    projection="v12-catalog-v1",
                    model=self.name,
                    models=True,
                    principal_id=actor.principal_id,
                    scope=dict(
                        products=sorted(actor.product_ids),
                        locations=sorted(actor.selling_location_ids),
                        channels=sorted(actor.channels),
                    ),
                    items=rows,
                )
            )
        else:
            digest = canonical_sha256(
                dict(
                    principal=actor.principal_id,
                    scope=query.model_dump(mode="json", exclude={"limit", "offset", "view_sha256"}),
                    items=rows,
                )
            )
        return type(value).model_validate_json(
            value.model_copy(update={"view_sha256": digest}).model_dump_json()
        )


def adapter(kind, *, state="alert", error=None):
    if kind == "risk":
        reader = Reader(risk_page("scored" if state == "alert" else state), error)
        inv = Reader(inventory_result())
        return NativeStockoutTool(reader, inv, "test"), reader
    if kind == "anomalies":
        reader = Reader(anomaly_page(state), error)
        return NativeAnomalyTool(reader, "test"), reader
    if kind == "operations":
        reader = Reader(operations_proof(state == "empty"), error)
        return NativeOperationsTool(reader, "test"), reader
    f, a = model_pages()
    reader = Reader(f, error)
    return NativeModelStatusTool(reader, Reader(a), "test"), reader


@pytest.mark.parametrize("kind", KINDS)
def test_lossless_native_roundtrip_and_narrowed_identity(kind):
    tool, reader = adapter(kind)
    broad = replace(
        actor(),
        product_ids=actor().product_ids | {"outside"},
        selling_location_ids=actor().selling_location_ids | {"outside"},
    )
    value = asyncio.run(tool.execute(request(kind), broad, None))
    assert value.status == "ok" and value.native_view.request == request(kind)
    assert OUTPUT.validate_json(value.model_dump_json()) == value
    assert reader.calls[0][1].product_ids == frozenset({PRODUCT})
    assert reader.calls[0][1].selling_location_ids == frozenset({STORE})
    assert reader.calls[0][1].channels == frozenset({"store"})
    assert len(broad.product_ids) == 2


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "fault", ["role", "capability", "products", "locations", "channel", "environment"]
)
def test_denial_and_environment_drift_precede_native_reads(kind, fault):
    tool, reader = adapter(kind)
    principal = actor()
    if fault == "role":
        principal = replace(principal, roles=frozenset({"viewer"}))
    elif fault == "capability":
        principal = replace(
            principal, capabilities=principal.capabilities - {READ_CAPABILITIES[KINDS[kind]]}
        )
    elif fault == "products":
        principal = replace(principal, product_ids=frozenset({"outside"}))
    elif fault == "locations":
        principal = replace(principal, selling_location_ids=frozenset({"outside"}))
    elif fault == "channel":
        principal = replace(principal, channels=frozenset({"online"}))
    else:
        reader.environment = "local"
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(tool.execute(request(kind), principal, None))
    assert caught.value.code == ("unavailable" if fault == "environment" else "unauthorized")
    assert reader.calls == []


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "error",
    [RuntimeError("private-native-marker"), ToolFailure("not_found"), asyncio.CancelledError()],
)
def test_native_private_errors_and_cancellation(kind, error):
    tool, _ = adapter(kind, error=error)
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(tool.execute(request(kind), actor(), None))
    else:
        with pytest.raises(ToolFailure) as caught:
            asyncio.run(tool.execute(request(kind), actor(), None))
        assert caught.value.code == (
            "not_found" if isinstance(error, ToolFailure) else "unavailable"
        )
        assert "private-native-marker" not in str(caught.value)


@pytest.mark.parametrize("kind", KINDS)
def test_executor_rejects_runtime_results_without_native_proof(kind):
    value = asyncio.run(adapter(kind)[0].execute(request(kind), actor(), None))
    raw = value.model_dump(mode="json")
    raw.pop("native_view")
    raw["items"] = []
    raw["status"] = "no_data"
    raw["freshness_status"] = "missing"
    forged = OUTPUT.validate_json(json.dumps(raw))

    class Forged:
        source_kind = "runtime"

        async def execute(self, *args):
            return forged

    authority, token = auth()
    session = ToolExecutor(
        authority, {KINDS[kind]: Forged()}, policy(), "test", clock=lambda: NOW
    ).open_session(token)
    with pytest.raises(ToolFailure, match="unavailable"):
        asyncio.run(session.execute_json(request(kind).model_dump_json()))
    assert not session.accepted_outputs()


@pytest.mark.parametrize("kind", KINDS)
def test_graph_known_native_facts_and_release_trace(kind):
    call = request(kind)
    query = graph_request(
        kind,
        scope=call.scope.model_dump(mode="json"),
        as_of=NOW.isoformat(),
        window=call.window.model_dump(mode="json")
        if hasattr(call, "window")
        else dict(start="2026-08-22", end="2026-08-22"),
    )
    tool, _ = adapter(kind)
    runner, token, query = harness(query, access=auth(), adapters={KINDS[kind]: tool})
    value = invoke(runner, token, query)
    assert value.status == "succeeded", value.model_dump_json()
    assert value.answer.outcome == "answered", value.model_dump_json()
    assert value.trace.fixture_only
    assert value.answer.evidence and value.trace.tools[0].source_refs
    if kind == "model":
        assert "not_attested" in value.answer.summary
        assert value.answer.data_freshness.predictions.status == "missing"
    elif kind == "operations":
        assert "consumer_lag_seconds=None" in value.answer.summary
    else:
        assert value.trace.release_refs


@pytest.mark.parametrize("state", ["already_stockout", "insufficient_data"])
def test_native_risk_unknown_probability_is_not_zero_or_one(state):
    value = asyncio.run(adapter("risk", state=state)[0].execute(request("risk"), actor(), None))
    assert value.status == "ok" and value.items[0].risk.probability is None
    assert value.items[0].risk.status == state and value.items[0].risk.risk_band is None


def test_native_anomaly_no_alert_does_not_create_unequal_units_suggestion():
    call = request("anomalies")
    query = graph_request(
        "anomalies",
        scope=call.scope.model_dump(mode="json"),
        as_of=NOW.isoformat(),
        window=call.window.model_dump(mode="json"),
    )
    runner, token, query = harness(
        query,
        access=auth(),
        adapters={KINDS["anomalies"]: adapter("anomalies", state="no_alert")[0]},
    )
    value = invoke(runner, token, query)
    assert value.answer.outcome == "answered" and "decision=no_alert" in value.answer.summary
    assert value.answer.recommended_actions == []


@pytest.mark.parametrize(
    "kind,state", [("anomalies", "insufficient_data"), ("operations", "empty")]
)
def test_native_unknown_scope_blocks_answer_without_measured_zero(kind, state):
    call = request(kind)
    query = graph_request(
        kind,
        scope=call.scope.model_dump(mode="json"),
        as_of=NOW.isoformat(),
        window=call.window.model_dump(mode="json")
        if hasattr(call, "window")
        else dict(start="2026-08-22", end="2026-08-22"),
    )
    runner, token, query = harness(
        query, access=auth(), adapters={KINDS[kind]: adapter(kind, state=state)[0]}
    )
    value = invoke(runner, token, query)
    assert value.answer.outcome == "insufficient_evidence" and value.answer.evidence == []


@pytest.mark.parametrize("kind", ["risk", "anomalies", "operations", "model"])
@pytest.mark.parametrize("fault", ["page", "scope", "source_kind", "items"])
def test_native_result_rejects_independent_binding_tampering(kind, fault):
    value = asyncio.run(adapter(kind)[0].execute(request(kind), actor(), None))
    raw = value.model_dump(mode="json")
    if fault == "page":
        proof = raw["native_view"]
        if kind == "operations":
            proof["points"].append(deepcopy(proof["points"][0]))
        else:
            page = proof["forecast"] if kind == "model" else proof["page"]
            page["pagination"]["total"] += 1
    elif fault == "scope":
        raw["native_view"]["request"]["scope"]["product_ids"] = ["outside"]
    elif fault == "source_kind":
        raw["source_kind"] = "fixture"
        raw["source_ref"] = "fixture-forged"
    else:
        raw["items"] = []
    with pytest.raises(ValidationError):
        OUTPUT.validate_json(json.dumps(raw))


def test_stockout_horizon_physical_scope_and_parent_pin_rejections():
    tool, reader = adapter("risk")
    wrong = request("risk", window=dict(start="2026-08-24", end="2026-08-29"))
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(tool.execute(wrong, actor(), None))
    assert caught.value.code == "invalid_scope" and reader.calls == []
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(
            tool.execute(
                request("risk"),
                replace(
                    actor(),
                    stockout=replace(actor().stockout, stock_location_ids=frozenset({"outside"})),
                ),
                None,
            )
        )
    assert caught.value.code == "unauthorized" and reader.calls == []
    raw = risk_page().model_dump(mode="json")
    raw["items"][0]["lineage"]["source_dataset_id"] = "source-sha256-" + "0" * 64
    reader.value = StockoutRiskPage.model_validate_json(json.dumps(raw))
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(tool.execute(request("risk"), actor(), None))
    assert caught.value.code == "unavailable"


@pytest.mark.parametrize(
    "fault", ["rows", "census", "decision", "release", "source", "model", "duplicate"]
)
def test_native_anomaly_verifies_whole_publication(fault):
    manifest, release, items = anomaly_publication()
    verify_publication(manifest, release, items)
    if fault == "rows":
        manifest["rows_sha256"] = "0" * 64
    elif fault == "census":
        manifest["descriptor"]["row_count"] = 2
    elif fault == "decision":
        manifest["descriptor"]["decisions_sha256"] = "0" * 64
    elif fault == "release":
        manifest["descriptor"]["release_id"] = "anomaly-release-sha256-" + "0" * 64
    elif fault == "source":
        manifest["descriptor"]["source_dataset_id"] = "source-sha256-" + "0" * 64
    elif fault == "model":
        manifest["descriptor"]["model_sha256"] = "0" * 64
    else:
        items *= 2
    with pytest.raises(ValueError):
        verify_publication(manifest, release, items)


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def mappings(self):
        return self

    def scalars(self):
        return self

    def one(self):
        return self.value

    def all(self):
        return self.value

    def __iter__(self):
        return iter(self.value)


class Engine:
    def __init__(self, handler):
        self.handler, self.queries = handler, []

    def connect(self):
        return self

    def execution_options(self, **options):
        self.options = options
        return self

    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params=None):
        statement = str(sql)
        self.queries.append((statement, params))
        return Result(self.handler(statement, params))


def test_postgres_anomaly_reader_verifies_complete_private_batch_then_projects():
    manifest, release, items = anomaly_publication()

    def handle(sql, params):
        if "clock_timestamp" in sql:
            return NOW
        if "SELECT b.batch_id" in sql:
            assert (
                params["products"] == [PRODUCT]
                and params["locations"] == [STORE]
                and params["cutoff"] == NOW
            )
            return manifest["batch_id"]
        if "release_complete" in sql:
            return dict(
                manifest=manifest,
                release=release.model_dump(mode="json"),
                binding=release.binding.model_dump(mode="json"),
                release_complete=True,
                enrollment_complete=True,
            )
        if "count(*) row_count" in sql:
            return dict(row_count=1, bytes=100)
        if "FROM ai.anomaly_results WHERE batch_id" in sql:
            return [i.model_dump(mode="json") for i in items]

    engine = Engine(handle)
    page = PostgresNativeAnomalyReader(engine, "test").read(request("anomalies"), actor())
    assert page.items == tuple(items)
    assert any("READ ONLY" in sql for sql, _ in engine.queries)
    assert any("statement_timeout='3s'" in sql for sql, _ in engine.queries)
    assert engine.options == dict(isolation_level="REPEATABLE READ")


@pytest.mark.parametrize(
    "fault", ["unknown", "future_update", "scope", "processed_clock", "duplicate", "budget"]
)
def test_postgres_operations_scope_and_time_without_global_health(fault):
    row = dict(
        event_id="event-1",
        product_id=PRODUCT,
        selling_location_id=STORE,
        channel="store",
        status="processed",
        ingested_at=NOW - timedelta(seconds=8),
        processed_at=NOW - timedelta(seconds=5),
        updated_at=NOW - timedelta(seconds=4),
    )
    if fault == "future_update":
        row["updated_at"] = NOW + timedelta(seconds=1)
    elif fault == "scope":
        row["product_id"] = "outside"
    elif fault == "processed_clock":
        row["processed_at"] = NOW - timedelta(seconds=10)
    rows = (
        []
        if fault == "unknown"
        else [row] * (10001 if fault == "budget" else 2 if fault == "duplicate" else 1)
    )

    def handle(sql, params):
        if "clock_timestamp" in sql:
            return NOW + timedelta(seconds=2)
        if "FROM realtime_event_log" in sql:
            assert params["products"] == [PRODUCT] and params["stores"] == [STORE]
            assert "payload->>'channel'=:channel" in sql and "LIMIT 10001" in sql
            assert "error_message" not in sql and "realtime_consumer_state" not in sql
            return rows

    engine = Engine(handle)
    reader = PostgresNativeOperationsReader(engine, "test")
    if fault in {"scope", "processed_clock", "duplicate", "budget"}:
        with pytest.raises((ValueError, ToolFailure)):
            reader.read(request("operations"), actor())
    else:
        value = reader.read(request("operations"), actor())
        assert not value.complete and value.points[0].stream_status == "not_observed"
        assert value.points[0].consumer_lag_seconds is None
        assert value.points[0].newer_state_not_evaluable == (1 if fault == "future_update" else 0)


@pytest.mark.parametrize(
    "kind,capability",
    [
        ("risk", "inventory:read"),
        ("anomalies", "anomaly:read"),
        ("model", "forecast:read"),
        ("model", "anomaly:read"),
    ],
)
def test_native_underlying_read_grants_are_never_synthesized(kind, capability):
    tool, reader = adapter(kind)
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(
            tool.execute(
                request(kind),
                replace(actor(), capabilities=actor().capabilities - {capability}),
                None,
            )
        )
    assert caught.value.code == "unauthorized" and reader.calls == []


def test_anomaly_partial_day_census_is_withheld_and_does_not_become_no_alert():
    tool, _ = adapter("anomalies")
    call = request("anomalies", window=dict(start="2026-08-21", end="2026-08-22"))
    value = asyncio.run(tool.execute(call, actor(), None))
    assert value.status == "no_data" and value.items == [] and not value.native_view.complete


def test_missing_native_risk_is_not_zero_and_missing_route_skips_risk_database():
    tool, reader = adapter("risk")
    raw = risk_page().model_dump(mode="json")
    raw["items"] = []
    raw["pagination"]["total"] = 0
    raw["data_status"] = "no_data"
    reader.value = StockoutRiskPage.model_validate_json(json.dumps(raw))
    value = asyncio.run(tool.execute(request("risk"), actor(), None))
    assert value.status == "no_data" and value.items == []
    reader.calls = []
    tool.inventory.value = inventory_result(inventory_proof("missing_route"))
    value = asyncio.run(tool.execute(request("risk"), actor(), None))
    assert value.status == "no_data" and value.native_view.page is None and reader.calls == []


@pytest.mark.parametrize("kind", KINDS)
def test_executor_recomputes_decision_time_freshness(kind):
    tool, _ = adapter(kind)
    authority, token = auth()
    session = ToolExecutor(
        authority, {KINDS[kind]: tool}, policy(), "test", clock=lambda: NOW
    ).open_session(token)
    session.executor.clock = lambda: NOW + timedelta(days=8)
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(session.execute_json(request(kind).model_dump_json()))
    assert caught.value.code in {"stale", "unavailable"} and session.accepted_outputs() == ()


def test_model_catalog_hash_binds_narrowed_scope_and_principal():
    forecast, anomaly = model_pages()

    class Frozen(Reader):
        models = Reader.take

    tool = NativeModelStatusTool(Frozen(forecast), Frozen(anomaly), "test")
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(tool.execute(request("model"), actor(), None))
    assert caught.value.code == "unavailable"


@pytest.mark.parametrize("kind", KINDS)
def test_http_native_routes_persist_required_grants_and_safe_trace(tmp_path, monkeypatch, kind):
    from pathlib import Path
    from uuid import UUID

    from test_assistant import CaptureStore, client

    from retailops_ai.agent.graph_config import load_graph_config
    from retailops_ai.assistant.contracts import AssistantQuery
    from retailops_ai.assistant.service import GraphAssistant

    authority, token = auth()
    path = tmp_path / "native-authority.json"
    path.write_text(authority._policy.model_dump_json())
    path.chmod(0o600)
    authenticate = LocalAccess.authenticate

    def frozen(self, authorization, *, now=None):
        return authenticate(self, authorization, now=now or NOW)

    monkeypatch.setattr(LocalAccess, "authenticate", frozen)
    call = request(kind)
    query = graph_request(
        kind,
        scope=call.scope.model_dump(mode="json"),
        as_of=NOW.isoformat(),
        window=call.window.model_dump(mode="json")
        if hasattr(call, "window")
        else dict(start="2026-08-22", end="2026-08-22"),
    )
    tool, _ = adapter(kind, state="no_alert" if kind == "anomalies" else "alert")
    graph = load_graph_config(Path(__file__).parents[1] / "agent/graph.fake.native-tools.v1.json")

    async def planner(body, principal):
        assert {str(v) for v in body.scope.product_ids} == set(call.scope.product_ids)
        assert {str(v) for v in body.scope.store_ids} == set(call.scope.selling_location_ids)
        return query

    def runner():
        return harness(
            query, resolved=graph, access=(authority, token), adapters={KINDS[kind]: tool}
        )[0]

    budget = graph.config.chat.budget
    backend = GraphAssistant(
        graph.config_id,
        graph.config.chat.knowledge_index_id,
        45.0,
        budget.max_input_tokens + budget.max_output_tokens,
        str(budget.pricing.max_run_cost),
        "fixture",
        planner,
        runner,
        native_tools=frozenset({KINDS[kind]}),
    )
    store = CaptureStore()
    body = AssistantQuery.model_validate_json(
        json.dumps(
            dict(
                question=query.question,
                scope=dict(
                    product_ids=[PRODUCT],
                    store_ids=[STORE],
                    **{"from": query.window.start.isoformat(), "to": query.window.end.isoformat()},
                ),
                conversation_id=None,
            )
        )
    )
    with client(path, store=store, backend=backend) as http:
        response = http.post(
            "/api/v1/assistant/queries",
            json=body.model_dump(mode="json", by_alias=True),
            headers={"Authorization": token},
        )
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer["outcome"] == "answered" and answer["evidence"]
        trace_id = UUID(answer["trace_id"])
        lease, trace = store.entries[trace_id]
        assert trace.tools[0].source_refs[0].endswith(lease.run.trace_id.hex) is False
        assert trace.tools[0].source_refs[0].split("-sha256-")[0] in {
            "anomaly-view",
            "stockout-view",
            "operations-view",
            "model-status-view",
        }
        assert {"assistant:query", READ_CAPABILITIES[KINDS[kind]]} <= set(
            lease.required_capabilities
        )
        if kind in {"anomalies", "model"}:
            assert "anomaly:read" in lease.required_capabilities
        if kind == "risk":
            assert "inventory:read" in lease.required_capabilities
        assert (
            http.get(
                "/api/v1/assistant/runs/" + answer["trace_id"], headers={"Authorization": token}
            ).status_code
            == 200
        )
        owner = authority.authenticate(token, now=NOW)
        revoked = replace(owner, capabilities=owner.capabilities - {READ_CAPABILITIES[KINDS[kind]]})
        assert asyncio.run(store.get(trace_id, revoked)) is None
