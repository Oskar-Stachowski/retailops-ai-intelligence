"""Physical mapping, causal native snapshots and whole-scope Assistant evidence."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_agent_graph import harness
from test_agent_graph import request as graph_request
from test_agent_tools import NOW, authority, policy
from test_inventory_curated import prepared as prepared
from test_inventory_curated import table_rows

from retailops_ai.adapters.native_inventory_tool import NativeInventoryReader, NativeInventoryTool
from retailops_ai.agent.execution import ToolExecutor, ToolFailure
from retailops_ai.agent.tools import (
    OUTPUT,
    InventoryRequest,
    InventoryResult,
    NativeInventoryEvidence,
)
from retailops_ai.domain.access import Principal, StockoutAccess
from retailops_ai.security.local import LocalAccess
from retailops_ai.security.models import AccessPolicy

PRODUCT = "22222222-2222-4222-8222-222222222222"
STORE = "33333333-3333-4333-8333-333333333333"
STOCK = "44444444-4444-4444-8444-444444444444"


def request():
    return InventoryRequest.model_validate_json(
        json.dumps(
            dict(
                schema_version="1.0",
                contract_type="tool_request",
                tool="get_inventory_status",
                scope=dict(product_ids=[PRODUCT], selling_location_ids=[STORE], channel="store"),
                as_of=NOW.isoformat(),
                limit=5,
            )
        )
    )


def proof(state="known", *, zero=False):
    value = request()
    route = dict(
        route_id="route-01",
        version=1,
        selling_location_id=STORE,
        stock_location_id=STOCK,
        channel="store",
        effective_from="2026-01-01",
        effective_to="2027-01-01",
        available_at="2026-01-01T00:00:00Z",
        curated_available_at="2026-01-01T00:00:00Z",
        source_record_sha256="a" * 64,
    )
    snapshot = dict(
        snapshot_id="snapshot-01",
        product_id=PRODUCT,
        stock_location_id=STOCK,
        business_date="2026-08-22",
        period_from_at="2026-08-22T00:00:00Z",
        period_to_at=NOW.isoformat(),
        is_full_business_day=True,
        snapshot_at=(NOW - timedelta(microseconds=1)).isoformat(),
        as_of_time=(NOW - timedelta(microseconds=1)).isoformat(),
        unit_of_measure="pcs",
        on_hand=0 if zero else 12,
        reserved_qty=0 if zero else 2,
        available_qty=0 if zero else 10,
        status="known",
        source_available_at="2026-08-22T23:00:00Z",
        curated_available_at=(NOW - timedelta(microseconds=1)).isoformat(),
        source_record_sha256="b" * 64,
    )
    if state == "missing_route":
        route, snapshot = None, None
    elif state == "missing_snapshot":
        snapshot = None
    elif state == "not_available":
        snapshot.update(
            status="not_available",
            on_hand=None,
            reserved_qty=None,
            available_qty=None,
            source_available_at=None,
            curated_available_at=None,
        )
    elif state == "stale":
        for name in ("period_to_at", "snapshot_at", "as_of_time", "curated_available_at"):
            snapshot[name] = (
                datetime.fromisoformat(snapshot[name]) - timedelta(minutes=10)
            ).isoformat()
    return NativeInventoryEvidence.model_validate_json(
        json.dumps(
            dict(
                environment="test",
                request=value.model_dump(mode="json"),
                source_dataset_id="source-sha256-" + "c" * 64,
                source_descriptor_sha256="c" * 64,
                curated_dataset_id="curated-sha256-" + "d" * 64,
                curated_descriptor_sha256="d" * 64,
                qualification_runtime_sha256="e" * 64,
                points=[
                    dict(
                        product_id=PRODUCT,
                        selling_location_id=STORE,
                        channel="store",
                        route=route,
                        snapshot=snapshot,
                        status=state,
                    )
                ],
            )
        )
    )


def result(evidence=None):
    evidence = evidence or proof()
    return InventoryResult(
        schema_version="1.0",
        contract_type="agent_tool_result",
        tool="get_inventory_status",
        source_kind="runtime",
        status="ok" if evidence.complete else "no_data",
        as_of=evidence.as_of,
        freshness_status="current" if evidence.complete else "missing",
        source_ref=evidence.view_ref,
        items=evidence.inventory_items(),
        error=None,
        native_view=evidence,
    )


def actor():
    return Principal(
        "inventory-operator",
        frozenset({"operator"}),
        frozenset({"assistant:query", "inventory:read"}),
        frozenset({PRODUCT}),
        frozenset({STORE}),
        frozenset({"store"}),
        stockout=StockoutAccess(frozenset({PRODUCT}), frozenset({STOCK})),
    )


class Reader:
    environment = "test"

    def __init__(self, output=None, error=None):
        self.output, self.error, self.calls = output or result(), error, []

    def read(self, value, principal):
        self.calls.append((value, principal))
        if self.error is not None:
            raise self.error
        return self.output


def access(value=None, stocks=None):
    value = value or request()
    auth, bearer = authority(capabilities=["assistant:query", "inventory:read", "stockout:read"])
    raw = auth._policy.model_dump(mode="json")
    raw["grants"][0]["scope"].update(
        product_ids=value.scope.product_ids,
        selling_location_ids=value.scope.selling_location_ids,
        channels=[value.scope.channel],
    )
    raw["grants"][0]["stockout_scope"].update(
        product_ids=value.scope.product_ids, stock_location_ids=stocks or [STOCK]
    )
    return LocalAccess(AccessPolicy.model_validate_json(json.dumps(raw))), bearer


def test_known_snapshot_keeps_physical_grain_timestamp_route_and_integer_quantities():
    value = result()
    assert OUTPUT.validate_json(value.model_dump_json()) == value
    assert value.as_of < value.native_view.request.as_of
    assert value.items[0].available_units == 10
    assert value.items[0].stock_location_id == STOCK != STORE
    assert value.items[0].mapping_ref == value.native_view.points[0].route.mapping_ref
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "contracts/agent/v1/native-inventory-evidence.v1.schema.json"
        ).read_bytes()
    )
    assert schema["properties"] == NativeInventoryEvidence.model_json_schema()["properties"]


@pytest.mark.parametrize("state", ["missing_route", "missing_snapshot", "not_available", "stale"])
def test_incomplete_scope_retains_reason_and_withholds_all_quantities(state):
    raw = proof().model_dump(mode="json")
    missing = proof(state).model_dump(mode="json")["points"][0]
    missing["product_id"] = STOCK
    if missing["snapshot"]:
        missing["snapshot"]["product_id"] = STOCK
    raw["request"]["scope"]["product_ids"].append(STOCK)
    raw["points"].append(missing)
    value = result(NativeInventoryEvidence.model_validate_json(json.dumps(raw)))
    assert value.items == [] and value.status == "no_data"
    assert value.native_view.points[0].snapshot.on_hand == 12


def test_explicit_zero_remains_known_and_unknown_cannot_be_zero():
    assert result(proof(zero=True)).items[0].available_units == 0
    assert result(proof("not_available")).items == []


@pytest.mark.parametrize(
    "mutation",
    [
        "partial",
        "duplicate",
        "product",
        "stock",
        "route_location",
        "route_channel",
        "route_future",
        "route_expired",
        "snapshot_future",
        "availability_future",
        "unknown_quantity",
        "reconciliation",
        "precision",
        "source",
        "curated",
        "limit",
        "status",
        "snapshot_time",
    ],
)
def test_rejects_partial_extra_noncausal_or_reshaped_evidence(mutation):
    raw = proof().model_dump(mode="json")
    point, value = raw["points"][0], raw["request"]
    if mutation == "partial":
        value["scope"]["product_ids"].append(STOCK)
    elif mutation == "duplicate":
        raw["points"].append(deepcopy(point))
    elif mutation == "product":
        point["snapshot"]["product_id"] = STOCK
    elif mutation == "stock":
        point["snapshot"]["stock_location_id"] = STORE
    elif mutation == "route_location":
        point["route"]["selling_location_id"] = STOCK
    elif mutation == "route_channel":
        point["route"]["channel"] = "online"
    elif mutation == "route_future":
        point["route"]["curated_available_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation == "route_expired":
        point["route"]["effective_to"] = NOW.date().isoformat()
    elif mutation == "snapshot_future":
        for key in ("snapshot_at", "as_of_time", "curated_available_at"):
            point["snapshot"][key] = (NOW + timedelta(seconds=1)).isoformat()
        point["snapshot"]["period_to_at"] = (NOW + timedelta(seconds=1, microseconds=1)).isoformat()
    elif mutation == "availability_future":
        point["snapshot"]["curated_available_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif mutation == "unknown_quantity":
        point["snapshot"]["status"] = "not_available"
    elif mutation == "reconciliation":
        point["snapshot"]["available_qty"] = 11
    elif mutation == "precision":
        point["snapshot"].update(on_hand=2**53 + 1, reserved_qty=0, available_qty=2**53 + 1)
    elif mutation in {"source", "curated"}:
        raw[mutation + "_descriptor_sha256"] = "f" * 64
    elif mutation == "limit":
        value["scope"]["product_ids"] += [STOCK, STORE]
        value["limit"] = 1
    elif mutation == "snapshot_time":
        point["snapshot"]["as_of_time"] = NOW.isoformat()
    else:
        point["status"] = "not_available"
    with pytest.raises(ValidationError):
        NativeInventoryEvidence.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field", ["items", "source_ref", "as_of", "freshness_status", "status"])
def test_result_cannot_claim_different_quantity_time_or_success(field):
    raw = result().model_dump(mode="json")
    if field == "items":
        raw[field][0].update(physical_units=13, available_units=11)
    elif field == "source_ref":
        raw[field] = "inventory-view-sha256-" + "f" * 64
    elif field == "as_of":
        raw[field] = NOW.isoformat()
    elif field == "freshness_status":
        raw[field] = "stale"
    else:
        raw.update(status="no_data", items=[], freshness_status="missing")
    with pytest.raises(ValidationError):
        InventoryResult.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "restriction",
    [
        "role",
        "assistant",
        "inventory",
        "product",
        "location",
        "channel",
        "physical_scope",
        "physical_product",
    ],
)
def test_unauthorized_requests_never_reach_reader(restriction):
    principal = actor()
    if restriction == "role":
        principal = replace(principal, roles=frozenset({"viewer"}))
    elif restriction in {"assistant", "inventory"}:
        principal = replace(
            principal,
            capabilities=principal.capabilities
            - {restriction + (":query" if restriction == "assistant" else ":read")},
        )
    elif restriction in {"product", "location", "channel"}:
        principal = replace(
            principal,
            **{
                {
                    "product": "product_ids",
                    "location": "selling_location_ids",
                    "channel": "channels",
                }[restriction]: frozenset({"outside"})
            },
        )
    elif restriction == "physical_scope":
        principal = replace(principal, stockout=None)
    else:
        principal = replace(
            principal, stockout=replace(principal.stockout, product_ids=frozenset({"outside"}))
        )
    reader = Reader()
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(NativeInventoryTool(reader, "test").execute(request(), principal, None))
    assert caught.value.code == "unauthorized" and reader.calls == []


def test_full_grid_budget_narrowing_and_physical_denial():
    broad = replace(
        actor(),
        product_ids=frozenset({PRODUCT, "outside"}),
        selling_location_ids=frozenset({STORE, "outside"}),
        stockout=replace(actor().stockout, product_ids=frozenset({PRODUCT, "outside"})),
    )
    reader = Reader()
    asyncio.run(NativeInventoryTool(reader, "test").execute(request(), broad, None))
    assert reader.calls == [(request(), actor())] and len(broad.product_ids) == 2
    raw = request().model_dump(mode="json")
    raw["scope"]["product_ids"].append("outside")
    raw["limit"] = 1
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(
            NativeInventoryTool(reader, "test").execute(
                InventoryRequest.model_validate_json(json.dumps(raw)), broad, None
            )
        )
    assert caught.value.code == "budget_exceeded" and len(reader.calls) == 1
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(
            NativeInventoryTool(Reader(), "test").execute(
                request(),
                replace(
                    actor(),
                    stockout=replace(actor().stockout, stock_location_ids=frozenset({STORE})),
                ),
                None,
            )
        )
    assert caught.value.code == "unauthorized"


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("private-inventory-marker"), ToolFailure("not_found"), asyncio.CancelledError()],
)
def test_private_errors_are_hidden_and_cancellation_preserved(failure):
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                NativeInventoryTool(Reader(error=failure), "test").execute(request(), actor(), None)
            )
    else:
        with pytest.raises(ToolFailure) as caught:
            asyncio.run(
                NativeInventoryTool(Reader(error=failure), "test").execute(request(), actor(), None)
            )
        assert caught.value.code == (
            "not_found" if isinstance(failure, ToolFailure) else "unavailable"
        )
        assert "private-inventory-marker" not in str(caught.value)


@pytest.mark.parametrize(
    "fault", ["unproved", "wrong_stock", "wrong_environment", "decision_stale"]
)
def test_executor_independently_checks_runtime_proof_physical_access_and_decision_age(fault):
    output = result()
    auth, bearer = access(stocks=[STORE] if fault == "wrong_stock" else None)

    class Runtime:
        source_kind = "runtime"

        async def execute(self, value, principal, pin):
            return output

    if fault == "unproved":
        output = output.model_copy(update={"native_view": None})
    elif fault == "wrong_environment":
        output = result(proof().model_copy(update={"environment": "local"}))
    run = ToolExecutor(
        auth,
        {"get_inventory_status": Runtime()},
        policy(freshness_seconds=30),
        "test",
        clock=lambda: NOW + timedelta(seconds=31 if fault == "decision_stale" else 0),
    )
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(run.open_session(bearer).execute_json(request().model_dump_json()))
    assert (
        caught.value.code
        == {
            "unproved": "unavailable",
            "wrong_stock": "unauthorized",
            "wrong_environment": "unavailable",
            "decision_stale": "stale",
        }[fault]
    )


@pytest.mark.parametrize("state", ["known", "not_available", "stale"])
def test_graph_keeps_native_snapshot_and_withholds_unknown_facts(state):
    output = result(proof(state))
    query = graph_request(
        "inventory", scope=request().scope.model_dump(mode="json"), as_of=NOW.isoformat()
    )
    runner, bearer, _ = harness(
        query,
        access=access(),
        cases=(),
        adapters={"get_inventory_status": NativeInventoryTool(Reader(output), "test")},
    )
    answer = asyncio.run(runner.run_json(bearer, query.model_dump_json()))
    assert answer.answer.outcome == ("answered" if state == "known" else "insufficient_evidence")
    if state == "known":
        assert "not additive" in answer.answer.evidence[0].claim
    else:
        assert answer.answer.evidence == [] and any(state in s for s in answer.answer.limitations)


@pytest.fixture(scope="module")
def verified_reader(prepared):
    return NativeInventoryReader(prepared[2].directory, prepared[1].directory, "test")


def fixture_request(prepared):
    snapshots = table_rows(prepared[2], "inventory_daily_snapshots")
    for row in snapshots:
        if row["status"] != "known":
            continue
        route = next(
            (
                r
                for r in table_rows(prepared[2], "inventory_fulfillment_routes")
                if r["channel"] == "store"
                and r["stock_location_id"] == row["stock_location_id"]
                and r["curated_available_at"] <= row["snapshot_at"]
                and r["effective_from"] <= row["snapshot_at"].date() < r["effective_to"]
            ),
            None,
        )
        if route:
            raw = request().model_dump(mode="json")
            raw.update(
                as_of=row["snapshot_at"].isoformat(),
                scope=dict(
                    product_ids=[row["product_id"]],
                    selling_location_ids=[route["selling_location_id"]],
                    channel=route["channel"],
                ),
            )
            return InventoryRequest.model_validate_json(json.dumps(raw)), row, route
    raise AssertionError("verified fixture has no known routed snapshot")


def fixture_actor(value, stocks):
    return replace(
        actor(),
        product_ids=frozenset(value.scope.product_ids),
        selling_location_ids=frozenset(value.scope.selling_location_ids),
        channels=frozenset({value.scope.channel}),
        stockout=StockoutAccess(frozenset(value.scope.product_ids), frozenset(stocks)),
    )


def test_verified_native_source_fixture_matches_independent_ledger_and_rebind(
    prepared, verified_reader
):
    value, row, route = fixture_request(prepared)
    output = verified_reader.read(value, fixture_actor(value, [route["stock_location_id"]]))
    movements = [
        r
        for r in table_rows(prepared[2], "inventory_ledger")
        if r["product_id"] == row["product_id"]
        and r["stock_location_id"] == row["stock_location_id"]
        and r["occurred_at"] <= value.as_of
        and r["available_at"] <= value.as_of
    ]
    assert (
        output.items[0].physical_units
        == sum(r["quantity_delta"] for r in movements)
        == row["on_hand"]
    )
    assert output.native_view.source_dataset_id == prepared[1].snapshot.source_id
    assert (
        NativeInventoryReader(prepared[2].directory, prepared[1].directory, "test").read(
            value, fixture_actor(value, [route["stock_location_id"]])
        )
        == output
    )


@pytest.mark.parametrize("mode", ["before_snapshot", "stale", "foreign_stock"])
def test_verified_fixture_cutoff_age_and_physical_grants(prepared, verified_reader, mode):
    value, row, route = fixture_request(prepared)
    raw = value.model_dump(mode="json")
    raw["as_of"] = (
        value.as_of + timedelta(seconds=301)
        if mode == "stale"
        else value.as_of - timedelta(microseconds=1)
        if mode == "before_snapshot"
        else value.as_of
    ).isoformat()
    value = InventoryRequest.model_validate_json(json.dumps(raw))
    principal = fixture_actor(
        value, [STORE if mode == "foreign_stock" else route["stock_location_id"]]
    )
    if mode == "foreign_stock":
        with pytest.raises(ToolFailure) as caught:
            verified_reader.read(value, principal)
        assert caught.value.code == "unauthorized"
    else:
        output = verified_reader.read(value, principal)
        assert output.status == "no_data" and output.items == []


@pytest.mark.parametrize("fault", ["newer_unknown", "future_route", "ambiguous_route"])
def test_verified_view_does_not_fall_back_or_choose_an_ambiguous_physical_mapping(
    prepared, verified_reader, fault
):
    from copy import copy

    from retailops_ai.agent.tools import NativeInventoryRoute, NativeInventorySnapshot

    value, row, route = fixture_request(prepared)
    reader = copy(verified_reader)
    reader._routes = dict(verified_reader._routes)
    reader._snapshots = dict(verified_reader._snapshots)
    key = (route["selling_location_id"], route["channel"])
    if fault == "newer_unknown":
        snapshot = next(
            s
            for s in reader._snapshots[row["product_id"], row["stock_location_id"]]
            if s.snapshot_id == row["snapshot_id"]
        )
        raw = snapshot.model_dump(mode="json")
        for field in ("period_from_at", "period_to_at", "snapshot_at", "as_of_time"):
            raw[field] = (datetime.fromisoformat(raw[field]) + timedelta(days=1)).isoformat()
        raw.update(
            snapshot_id="explicit-newer-unknown-fixture",
            business_date=(snapshot.business_date + timedelta(days=1)).isoformat(),
            status="not_available",
            on_hand=None,
            reserved_qty=None,
            available_qty=None,
            source_available_at=None,
            curated_available_at=None,
        )
        missing = NativeInventorySnapshot.model_validate_json(json.dumps(raw))
        reader._snapshots[row["product_id"], row["stock_location_id"]] = (snapshot, missing)
        raw_request = value.model_dump(mode="json") | {"as_of": missing.snapshot_at.isoformat()}
        value = InventoryRequest.model_validate_json(json.dumps(raw_request))
    else:
        selected = next(r for r in reader._routes[key] if r.route_id == route["id"])
        raw = selected.model_dump(mode="json") | {
            "route_id": "explicit-conflicting-route-fixture",
            "stock_location_id": STORE,
        }
        if fault == "future_route":
            raw.update(
                version=selected.version + 1,
                curated_available_at=(value.as_of + timedelta(seconds=1)).isoformat(),
            )
        changed = NativeInventoryRoute.model_validate_json(json.dumps(raw))
        reader._routes[key] = (selected, changed)
    if fault == "ambiguous_route":
        with pytest.raises(ToolFailure) as caught:
            reader.read(value, fixture_actor(value, [route["stock_location_id"], STORE]))
        assert caught.value.code == "unavailable"
    else:
        output = reader.read(value, fixture_actor(value, [route["stock_location_id"]]))
        assert output.status == ("no_data" if fault == "newer_unknown" else "ok")
        if fault == "newer_unknown":
            assert output.items == [] and output.native_view.points[0].status == "not_available"
        else:
            assert output.items[0].stock_location_id == route["stock_location_id"]


def test_reader_environment_drift_fails_before_read():
    reader = Reader()
    adapter = NativeInventoryTool(reader, "test")
    reader.environment = "local"
    with pytest.raises(ToolFailure) as caught:
        asyncio.run(adapter.execute(request(), actor(), None))
    assert caught.value.code == "unavailable" and reader.calls == []


def test_resealed_quantity_without_source_parent_is_rejected(prepared, tmp_path):
    import hashlib
    import shutil

    import pyarrow as pa
    import pyarrow.parquet as pq

    from retailops_ai.curated.builder import iter_rows, verify_curated
    from retailops_ai.curated.contract import Config, Digest, descriptor_id, source_contract
    from retailops_ai.curated.inventory import transform_inventory
    from retailops_ai.curated.transform import Index
    from retailops_ai.source_snapshot.files import SnapshotError, canonical_json

    root = Path(shutil.copytree(prepared[2].directory, tmp_path / "resealed"))
    manifest = json.loads((root / "curated_manifest.json").read_bytes())
    name = "inventory_daily_snapshots"
    table = next(t for t in manifest["tables"] if t["table"] == name)
    ref = table["files"][0]
    path = root / ref["path"]
    arrow = pq.ParquetFile(path).read()
    rows = arrow.to_pylist()
    row = next(r for r in rows if r["status"] == "known")
    row["on_hand"] += 1
    row["available_qty"] += 1
    # Recompute even the causal projection and source-row hash. Curated alone
    # accepts these plausible values; the pinned Source reconstruction must not.
    specs = source_contract(manifest["schema_version"])["fact_tables"]
    index = Index(tmp_path / "index.sqlite", specs)
    try:
        for spec in manifest["tables"]:
            for item in iter_rows(root, spec["files"], 8192):
                index.add(
                    spec["table"],
                    {c["name"]: item[c["name"]] for c in specs[spec["table"]]["schema"]},
                )
        index.db.commit()
        native = {c["name"]: row[c["name"]] for c in specs[name]["schema"]}
        row.update(transform_inventory(name, native, table["grain"], index, Config()))
    finally:
        index.close()
    pq.write_table(pa.Table.from_pylist(rows, schema=arrow.schema), path)
    ref.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    digest = Digest(tmp_path / "digest.sqlite", table["schema"], table["grain"])
    try:
        for item in iter_rows(root, table["files"], 8192):
            digest.add(item)
        table.update(digest.summary())
    finally:
        digest.close()
    manifest["descriptor"]["tables"] = [
        {k: v for k, v in t.items() if k != "files"} for t in manifest["tables"]
    ]
    manifest["curated_dataset_id"] = descriptor_id(manifest["descriptor"])
    raw = canonical_json(manifest) + b"\n"
    (root / "curated_manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    assert verify_curated(root)["readiness"]["inventory_ready"]
    with pytest.raises(SnapshotError, match="source_curated_mismatch"):
        NativeInventoryReader(root, prepared[1].directory, "test")


@pytest.mark.parametrize("mode", ["known", "stale"])
def test_verified_fixture_reaches_http_reviewed_planner_graph_and_stored_trace(
    prepared, verified_reader, tmp_path, mode, monkeypatch
):
    from test_assistant import CaptureStore, client, headers, setup

    from retailops_ai.agent.chat_context import tool_result_ref
    from retailops_ai.agent.graph_config import load_graph_config
    from retailops_ai.assistant.routes import load_question_routes, reviewed_backend
    from retailops_ai.assistant.source_catalog import load_source_catalog

    value, row, route = fixture_request(prepared)
    now = value.as_of + timedelta(seconds=301 if mode == "stale" else 0)
    path, tokens, _, body, _, _ = setup(
        tmp_path, capabilities=["assistant:query", "inventory:read"]
    )
    raw = json.loads(path.read_bytes())
    grant = raw["grants"][0]
    grant["capabilities"].append("stockout:read")
    grant["scope"].update(
        product_ids=value.scope.product_ids, selling_location_ids=value.scope.selling_location_ids
    )
    grant["stockout_scope"] = dict(
        product_ids=value.scope.product_ids, stock_location_ids=[route["stock_location_id"]]
    )
    for credential in raw["credentials"]:
        credential.update(
            not_before=(now - timedelta(seconds=1)).isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat(),
        )
    path.write_text(json.dumps(raw))
    auth = LocalAccess(AccessPolicy.model_validate_json(json.dumps(raw)))
    # The native fixture is historical. Freeze identity time in this test only,
    # retaining the same one-hour credential budget used by the API and graph.
    authenticate = LocalAccess.authenticate
    frozen_now = now

    def frozen_authenticate(self, authorization, *, now=None):
        return authenticate(self, authorization, now=now or frozen_now)

    monkeypatch.setattr(LocalAccess, "authenticate", frozen_authenticate)
    root = Path(__file__).parents[1]
    graph = load_graph_config(root / "agent/graph.evaluate.fake.prepaid.v5.json")
    routes = load_question_routes(root / "agent/question-routes.prepaid.proposed.v5.json")
    body["question"] = next(r.question for r in routes.routes if r.intent == "inventory")
    body["scope"].update(
        product_ids=value.scope.product_ids,
        store_ids=value.scope.selling_location_ids,
        **{"from": row["business_date"].isoformat(), "to": row["business_date"].isoformat()},
    )

    def runner():
        instance, _, _ = harness(
            resolved=graph,
            access=(auth, headers(tokens)["Authorization"]),
            cases=(),
            adapters={"get_inventory_status": NativeInventoryTool(verified_reader, "test")},
        )
        instance.executor.clock = lambda: now
        return instance

    backend = reviewed_backend(
        routes,
        graph,
        load_source_catalog(prepared[1].directory),
        "store",
        frozenset({"get_inventory_status"}),
        "test",
        "fixture",
        runner,
        allow_proposed=True,
        clock=lambda: now,
    )
    store = CaptureStore()
    with client(path, store=store, backend=backend) as http:
        response = http.post("/api/v1/assistant/queries", json=body, headers=headers(tokens))
        assert response.status_code == 200, response.text
        answer = response.json()
        trace = http.get("/api/v1/assistant/runs/" + answer["trace_id"], headers=headers(tokens))
        assert trace.status_code == 200
        assert answer["outcome"] == ("answered" if mode == "known" else "insufficient_evidence")
        if mode == "known":
            expected = verified_reader.read(
                value, fixture_actor(value, [route["stock_location_id"]])
            )
            assert answer["evidence"][0]["source_ref"] == tool_result_ref(expected)
            assert trace.json()["tools"][0]["source_refs"] == [expected.source_ref]
        else:
            assert answer["evidence"] == [] and any("stale" in v for v in answer["limitations"])


@pytest.mark.parametrize("count,max_rows,expected", [(6, 20, 6), (3, 2, None)])
def test_inventory_planner_covers_entire_scope_or_rejects_before_admission(
    count, max_rows, expected
):
    from uuid import NAMESPACE_DNS, uuid5

    from test_assistant_routes import GRAPH, PROFILE, catalog, principal, query

    from retailops_ai.agent.graph_config import AgentGraphConfig, resolve_graph_config
    from retailops_ai.assistant.contracts import AssistantQuery
    from retailops_ai.assistant.routes import QuestionRoutes, ReviewedPlanner
    from retailops_ai.assistant.service import AssistantError
    from retailops_ai.assistant.source_catalog import CatalogProduct

    products = [str(uuid5(NAMESPACE_DNS, "inventory-planner-" + str(i))) for i in range(count)]
    source = catalog().model_copy(
        update={
            "products": tuple(
                CatalogProduct(product_id=p, available_at=NOW - timedelta(days=100))
                for p in products
            )
        }
    )
    identity = replace(
        principal(),
        product_ids=frozenset(products),
        stockout=StockoutAccess(frozenset(products), frozenset({STOCK})),
    )
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
        frozenset({"get_inventory_status"}),
        "test",
        allow_proposed=True,
        clock=lambda: NOW,
    )
    raw = query("inventory").model_dump(mode="json", by_alias=True)
    raw["scope"]["product_ids"] = products
    value = AssistantQuery.model_validate_json(json.dumps(raw))
    if expected is None:
        with pytest.raises(AssistantError) as caught:
            asyncio.run(planner.prepare(value, identity))
        assert caught.value.status == 422
    else:
        assert asyncio.run(planner.prepare(value, identity)).limit == expected
