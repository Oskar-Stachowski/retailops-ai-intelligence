import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_agent_tools import KnowledgeSpy, authority, example
from test_agent_tools import semantic_pin as semantic_pin
from test_chunks import config as config
from test_chunks import sources as sources
from test_semantic_embeddings import real_config as real_config

from retailops_ai.adapters.agent_tools import FixtureTools, PinnedKnowledgeTool
from retailops_ai.adapters.fake_chat import FakeChatStep, ScriptedChatProvider
from retailops_ai.agent.chat_contracts import ProviderReply
from retailops_ai.agent.evidence import required_calls
from retailops_ai.agent.execution import ToolExecutor
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import (
    AgentGraphConfig,
    load_graph_config,
    resolve_graph_config,
)
from retailops_ai.agent.graph_contracts import GraphRequest
from retailops_ai.agent.graph_traces import MemoryTraces, TraceUnavailable
from retailops_ai.agent.tools import OUTPUT
from retailops_ai.cli import main
from retailops_ai.data_contracts.identity import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "agent/graph.fake.native-tools.v1.json"
PRIVATE = "private-graph-secret-marker"


def settings(**changes):
    value = json.loads(CONFIG.read_text())

    def merge(left, right):
        for key, child in right.items():
            if isinstance(child, dict):
                merge(left[key], child)
            else:
                left[key] = child

    merge(value, changes)
    return resolve_graph_config(AgentGraphConfig.model_validate_json(json.dumps(value)))


def request(intent="sales", **updates):
    value = {
        "schema_version": "1.0",
        "question": "What evidence is available?",
        "intent": intent,
        "scope": example()["scope"],
        "as_of": example()["as_of"],
        "window": example()["window"],
        "comparison_window": None,
        "limit": 5,
    }
    if intent in {"forecast", "risk"}:
        value["window"] = {"start": "2026-08-23", "end": "2026-08-29"}
    if intent == "sales_comparison":
        value["comparison_window"] = {"start": "2026-08-09", "end": "2026-08-15"}
    return GraphRequest.model_validate_json(json.dumps(value | updates))


def fixture_outputs(query, *, change=None):
    cases = []
    for call in required_calls(query):
        if call.tool == "search_knowledge":
            continue
        raw = example(call.tool, "result")
        if call.tool == "get_demand_forecast":
            raw["result"]["request"] = call.model_dump(mode="json")
        elif hasattr(call, "window"):
            for row in raw["items"]:
                row["window"] = call.window.model_dump(mode="json")
                if call.tool == "get_sales_summary":
                    row["observed_sales_units"] = (
                        0.3 if row["window"] == query.window.model_dump(mode="json") else 0.1
                    )
        if change:
            change(call, raw)
        cases.append((call, OUTPUT.validate_json(json.dumps(raw))))
    return cases


class PolicyFixture(ScriptedChatProvider):
    """Copies a deterministic script from typed policy input, not a quality test of an LLM."""

    def __init__(self, model, *, empty_plans=0, transform=None, events=None, delay=0):
        super().__init__(model, (FakeChatStep("unconfigured", "schema"),))
        self.empty_plans = empty_plans
        self.transform = transform
        self.events = events or []
        self.delay = delay
        self.seen = []
        self.started = asyncio.Event()

    def actions(self, policy, claims):
        result = []
        for candidate in policy.get("suggestion_candidates", []):
            grain = f"product={candidate['product_id']}; selling_location={candidate['selling_location_id']}; channel={candidate['channel']}"
            refs = {claim["source_ref"] for claim in claims if grain in claim["claim"]}
            if set(candidate["evidence_refs"]) <= refs:
                result.append(
                    {
                        key: candidate[key]
                        for key in (
                            "action",
                            "priority",
                            "rationale",
                            "evidence_refs",
                            "requires_human_review",
                        )
                    }
                )
        return result

    def input_token_bound(self, request):
        return 100

    async def generate(self, request):
        self.started.set()
        self.seen.append(request)
        policy = json.loads(request.references_json)["server_evidence_policy"]
        if request.expected_kind == "tool_plan":
            calls = [] if self.empty_plans > 0 else policy["permitted_calls"]
            self.empty_plans -= int(self.empty_plans > 0)
            body = {"kind": "tool_plan", "tools": calls}
        else:
            claims = [fact["evidence"] for fact in policy["facts"]][:5]
            refs = {claim["source_ref"] for claim in claims if claim["source_type"] == "document"}
            candidates = json.loads(request.references_json)["citation_candidates"]
            body = {
                "kind": "answer",
                "outcome": policy["expected_outcome"],
                "summary": "\n".join(claim["claim"] for claim in claims),
                "evidence": claims,
                "recommended_actions": self.actions(policy, claims),
                "confidence": "medium",
                "data_freshness": policy["data_freshness"],
                "citations": [
                    citation for citation in candidates if citation["source_ref"] in refs
                ],
                "limitations": policy["limitations"],
            }
        if self.transform:
            body = self.transform(request, deepcopy(body))
        event = self.events[self.calls] if self.calls < len(self.events) else "reply"
        reply = ProviderReply.model_validate_json(
            json.dumps(
                {
                    "body": body if isinstance(body, str) else json.dumps(body),
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                }
            )
        )
        self.steps = self.steps[: self.calls] + (
            FakeChatStep(request.request_hash(), event, reply.model_dump_json()),
        )
        await asyncio.sleep(self.delay)
        return await super().generate(request)


def harness(
    query=None,
    *,
    resolved=None,
    access=None,
    cases=None,
    adapters=None,
    provider_options=None,
    pin=None,
    timer=None,
):
    query = query or request()
    resolved = resolved or settings()
    auth, bearer = access or authority()
    cases = cases if cases is not None else fixture_outputs(query)
    fixture = FixtureTools(cases)
    adapters = adapters if adapters is not None else {call.tool: fixture for call, _ in cases}
    executor = ToolExecutor(
        auth,
        adapters,
        resolved.config.chat.tool_policy,
        "test",
        allow_fixtures=True,
        clock=lambda: __import__("test_agent_tools").NOW,
        **({"timer": timer} if timer else {}),
    )
    provider = PolicyFixture(resolved.config.chat.model, **(provider_options or {}))
    traces = MemoryTraces(resolved.config.policy, **({"timer": timer} if timer else {}))
    runner = GraphRunner(executor, resolved, provider, traces, pin=pin)
    return runner, bearer, query


def invoke(runner, bearer, query):
    return asyncio.run(runner.run_json(bearer, query.model_dump_json()))


@pytest.mark.parametrize(
    "intent",
    [
        "sales",
        "sales_comparison",
        "inventory",
        "forecast",
        "risk",
        "anomalies",
        "operations",
        "model",
        "investigation",
    ],
)
def test_complete_graph_returns_only_typed_grounded_claims_and_a_safe_trace(intent):
    runner, bearer, query = harness(request(intent))
    result = invoke(runner, bearer, query)
    assert result.status == "succeeded", result.model_dump_json()
    assert result.answer.outcome == "answered"
    assert result.answer.summary == "\n".join(claim.claim for claim in result.answer.evidence)
    assert all(action.requires_human_review for action in result.answer.recommended_actions)
    assert result.trace.fixture_only and result.trace.model_calls == 2
    assert (
        result.trace.nodes[0].node == "validate_auth"
        and result.trace.nodes[-1].node == "persist_trace"
    )
    principal = runner.executor.authority.authenticate(bearer, now=runner.executor.clock())
    assert runner.traces.get(result.trace.trace_id, principal) == result.trace
    assert query.question not in result.trace.model_dump_json()
    if intent == "sales_comparison":
        calculation = next(
            claim for claim in result.answer.evidence if claim.source_type == "calculation"
        )
        assert "0.3 unit - 0.1 unit = 0.2 unit" in calculation.claim
        assert calculation.calculation_id == "sales-period-difference-v1"
        assert len(set(calculation.supporting_refs)) == 2
        assert (
            "2026-08-09..2026-08-15" in calculation.claim
            and "2026-08-16..2026-08-22" in calculation.claim
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "number",
        "unit",
        "grain",
        "period",
        "meaning",
        "summary",
        "confidence",
        "limitations",
        "replenishment",
        "causality",
    ],
)
def test_matching_reference_does_not_authorize_a_false_number_or_semantic_claim(mutation):
    def transform(req, body):
        if req.expected_kind != "answer":
            return body
        if mutation == "number":
            body["evidence"][0]["claim"] = body["evidence"][0]["claim"].replace("0.3", "999")
        elif mutation == "unit":
            body["evidence"][0]["claim"] = body["evidence"][0]["claim"].replace("unit", "USD")
        elif mutation == "grain":
            body["evidence"][0]["claim"] = body["evidence"][0]["claim"].replace("p-101", "p-999")
        elif mutation == "period":
            body["evidence"][0]["claim"] = body["evidence"][0]["claim"].replace(
                "2026-08-16", "2026-08-01"
            )
        elif mutation == "meaning":
            body["evidence"][0]["claim"] = "Uncensored demand was measured; the model is deployed."
        elif mutation == "summary":
            body["summary"] = "Verified sales were 999 and the system is deployed."
        elif mutation == "confidence":
            body["confidence"] = "high"
        elif mutation == "limitations":
            body["limitations"] = []
        elif mutation == "causality":
            body["summary"] = "The price caused lower sales."
        else:
            body["recommended_actions"] = [
                {
                    "action": "Order 100 units",
                    "priority": "high",
                    "rationale": "An invented quantity",
                    "evidence_refs": [body["evidence"][0]["source_ref"]],
                    "requires_human_review": True,
                }
            ]
        return body

    runner, bearer, query = harness(provider_options={"transform": transform})
    result = invoke(runner, bearer, query)
    assert result.status == "failed" and result.error_code == "invalid_evidence"
    assert result.answer is None and result.trace.repairs == 1
    assert result.trace.model_calls == 3


def test_wrong_calculation_is_rejected_even_with_both_real_source_refs():
    def transform(req, body):
        if req.expected_kind == "answer":
            claim = next(
                claim for claim in body["evidence"] if claim["source_type"] == "calculation"
            )
            claim["claim"] = claim["claim"].replace("= 0.2 unit", "= 0.5 unit")
        return body

    runner, bearer, query = harness(
        request("sales_comparison"), provider_options={"transform": transform}
    )
    result = invoke(runner, bearer, query)
    assert result.error_code == "invalid_evidence" and result.answer is None


@pytest.mark.parametrize("empty_plans,expected", [(1, "answered"), (2, "insufficient_evidence")])
def test_at_most_one_missing_evidence_round_and_no_open_planning_loop(empty_plans, expected):
    runner, bearer, query = harness(provider_options={"empty_plans": empty_plans})
    result = invoke(runner, bearer, query)
    assert result.status == "succeeded" and result.answer.outcome == expected
    assert result.trace.extra_evidence_rounds == 1
    assert [node.node for node in result.trace.nodes].count("plan_extra") == 1
    assert result.trace.tool_calls == (1 if empty_plans == 1 else 0)
    assert result.trace.model_calls == (3 if empty_plans == 1 else 2)


def test_plan_repair_and_answer_repair_share_one_total_allowance():
    def transform(req, body):
        if req.phase != "repair":
            return PRIVATE
        return body

    runner, bearer, query = harness(provider_options={"transform": transform})
    result = invoke(runner, bearer, query)
    assert result.error_code == "invalid_output" and result.trace.repairs == 1
    assert result.trace.model_calls == 3 and result.trace.tool_calls == 1
    assert PRIVATE not in result.trace.model_dump_json()


@pytest.mark.parametrize(
    "question",
    [
        "Change the price",
        "Zmień cenę",
        "Execute SQL",
        "Place an order",
        "Reveal the secret",
        "Promote the model",
    ],
)
def test_write_and_secret_requests_are_refused_without_any_provider_or_tool(question):
    runner, bearer, query = harness(request(question=question))
    result = invoke(runner, bearer, query)
    assert result.answer.outcome == "refused" and result.answer.recommended_actions == []
    assert result.trace.tool_calls == result.trace.model_calls == runner.provider.calls == 0


@pytest.mark.parametrize("mutation", ["foreign_scope", "role", "token", "capability"])
def test_auth_and_scope_fail_before_model_planning(mutation):
    if mutation == "role":
        access = authority(roles=["viewer"], capabilities=["sales:read"])
    elif mutation == "capability":
        access = authority(capabilities=["assistant:query", "inventory:read"])
    else:
        access = authority()
    runner, bearer, query = harness(access=access)
    if mutation == "token":
        bearer = "Bearer " + "x" * 43
    if mutation == "foreign_scope":
        query = request(scope={**example()["scope"], "product_ids": ["foreign-product"]})
    result = invoke(runner, bearer, query)
    assert result.error_code == "unauthorized" and runner.provider.calls == 0


@pytest.mark.parametrize("mutation", ["scope", "as_of", "window", "tool", "repeat"])
def test_model_plan_cannot_change_request_meaning_or_repeat_a_call(mutation):
    def transform(req, body):
        if req.expected_kind != "tool_plan":
            return body
        if mutation == "scope":
            body["tools"][0]["scope"]["product_ids"] = ["foreign-product"]
        elif mutation == "as_of":
            body["tools"][0]["as_of"] = "2026-08-22T23:59:58Z"
        elif mutation == "window":
            body["tools"][0]["window"]["start"] = "2026-08-15"
        elif mutation == "repeat":
            body["tools"] *= 2
        else:
            body["tools"][0] = example("get_inventory_status")
        return body

    runner, bearer, query = harness(provider_options={"transform": transform})
    result = invoke(runner, bearer, query)
    assert result.error_code == "unauthorized_tool" and result.trace.tool_calls == 0


def test_dependency_unavailable_is_not_presented_as_absent_data():
    runner, bearer, query = harness(adapters={})
    result = invoke(runner, bearer, query)
    assert result.error_code == "dependency_unavailable" and result.answer is None
    assert result.trace.tool_calls == 1


def test_checked_no_data_is_insufficient_and_never_a_fabricated_zero():
    def empty(call, raw):
        raw.update(status="no_data", freshness_status="missing", items=[])

    query = request()
    runner, bearer, query = harness(query, cases=fixture_outputs(query, change=empty))
    result = invoke(runner, bearer, query)
    assert result.answer.outcome == "insufficient_evidence" and result.answer.evidence == []
    assert result.answer.data_freshness.sales.status == "missing"
    assert any("not a measured zero" in text for text in result.answer.limitations)


def test_stale_inventory_and_ambiguous_mapping_do_not_support_current_facts():
    def stale(call, raw):
        raw["freshness_status"] = "stale"

    query = request("inventory")
    runner, bearer, query = harness(query, cases=fixture_outputs(query, change=stale))
    result = invoke(runner, bearer, query)
    assert result.answer.outcome == "insufficient_evidence"
    assert any("stale" in text for text in result.answer.limitations)

    def ambiguous(call, raw):
        raw["items"].append({**raw["items"][0], "stock_location_id": "warehouse-02"})

    runner, bearer, query = harness(query, cases=fixture_outputs(query, change=ambiguous))
    result = invoke(runner, bearer, query)
    assert result.answer.outcome == "insufficient_evidence"
    assert any("Multiple stock locations" in text for text in result.answer.limitations)


def test_conflicting_forecasts_are_reported_without_selecting_one_as_truth():
    def conflict(call, raw):
        second = deepcopy(raw["result"]["items"][0])
        second["predicted_units"] += 1.0
        second["prediction_id"] = "prediction-sha256-" + canonical_sha256(
            {
                key: value
                for key, value in second.items()
                if key
                not in {
                    "prediction_id",
                    "prediction_dataset_id",
                    "generated_at",
                    "freshness_status",
                }
            }
        )
        raw["result"]["items"].append(second)

    query = request("forecast")
    runner, bearer, query = harness(query, cases=fixture_outputs(query, change=conflict))
    result = invoke(runner, bearer, query)
    assert result.answer.outcome == "insufficient_evidence" and result.answer.evidence == []
    assert any("Conflicting values" in text for text in result.answer.limitations)


def test_document_quote_is_cited_with_its_status_and_cannot_become_a_deployment_claim(semantic_pin):
    pin, candidate = semantic_pin
    # This test authorizes one known fixture excerpt for this exact synthetic question.
    # Generic retrieval results alone are deliberately insufficient.
    chunk = candidate.chunks.chunks[0]
    query = request("documentation")
    resolved = settings(
        chat={
            "knowledge_index_id": pin.manifest.index_id,
            "embeddings": pin.manifest.embedding_config.model_dump(mode="json"),
        },
        policy={
            "document_rules": [
                {
                    "question": query.question,
                    "intent": "documentation",
                    "requirements": [
                        {
                            "requirement_id": "fixture-evidence",
                            "description": "The explicitly selected fixture excerpt",
                            "supports": [
                                {
                                    "chunk_id": chunk.chunk_id,
                                    "chunk_sha256": canonical_sha256(chunk.model_dump(mode="json")),
                                    "quote": chunk.text[:240],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    runner, bearer, query = harness(
        query,
        resolved=resolved,
        pin=pin,
        adapters={"search_knowledge": PinnedKnowledgeTool(KnowledgeSpy(candidate))},
    )
    result = invoke(runner, bearer, query)
    assert result.status == "succeeded" and result.answer.outcome == "answered"
    assert result.answer.citations and result.trace.index_id == pin.manifest.index_id
    assert all("literal quote=" in claim.claim for claim in result.answer.evidence)

    def deploy(req, body):
        if req.expected_kind == "answer":
            body["evidence"][0]["claim"] = "EKS has been deployed and verified."
        return body

    runner, bearer, query = harness(
        query,
        resolved=resolved,
        pin=pin,
        adapters={"search_knowledge": PinnedKnowledgeTool(KnowledgeSpy(candidate))},
        provider_options={"transform": deploy},
    )
    result = invoke(runner, bearer, query)
    assert result.error_code == "invalid_evidence" and result.answer is None


def test_safe_trace_has_owner_scope_retention_and_capacity_limits():
    clock = [0.0]
    resolved = settings(policy={"trace_retention_seconds": 1, "trace_capacity": 1})
    runner, bearer, query = harness(resolved=resolved, timer=lambda: clock[0])
    result = invoke(runner, bearer, query)
    principal = runner.executor.authority.authenticate(bearer, now=runner.executor.clock())
    assert runner.traces.get(result.trace.trace_id, principal) == result.trace
    foreign, other_bearer = authority()
    other = foreign.authenticate(other_bearer, now=runner.executor.clock())
    from dataclasses import replace

    with pytest.raises(TraceUnavailable):
        runner.traces.get(result.trace.trace_id, replace(other, principal_id="foreign-owner"))
    with pytest.raises(TraceUnavailable):
        runner.traces.get(result.trace.trace_id, replace(principal, product_ids=frozenset()))
    full = invoke(runner, bearer, query)
    assert full.error_code == "trace_unavailable" and full.answer is None
    clock[0] = 1.0
    with pytest.raises(TraceUnavailable):
        runner.traces.get(result.trace.trace_id, principal)
    assert invoke(runner, bearer, query).status == "succeeded"


@pytest.mark.parametrize(
    "configuration,code",
    [
        ({"chat": {"budget": {"max_calls": 1}}}, "budget_exceeded"),
        ({"chat": {"budget": {"provider_timeout_seconds": 0.01}}}, "provider_unavailable"),
        ({"chat": {"tool_policy": {"request_deadline_seconds": 0.01}}}, "deadline_exceeded"),
    ],
)
def test_graph_deadline_provider_timeout_and_budget_are_controlled(configuration, code):
    opts = {"delay": 0.02} if code != "budget_exceeded" else {}
    runner, bearer, query = harness(resolved=settings(**configuration), provider_options=opts)
    result = invoke(runner, bearer, query)
    assert result.error_code == code and result.answer is None


def test_cancellation_consumes_the_attempt_and_records_safe_metadata():
    async def scenario():
        runner, bearer, query = harness(provider_options={"delay": 1})
        task = asyncio.create_task(runner.run_json(bearer, query.model_dump_json()))
        await asyncio.wait_for(runner.provider.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runner.traces._entries
        trace = json.loads(next(iter(runner.traces._entries.values()))[1])
        assert trace["model_calls"] == 1 and trace["error_code"] == "cancelled"
        assert query.question not in json.dumps(trace)

    asyncio.run(scenario())


def test_graph_does_not_inherit_remote_tracing_or_checkpointing(monkeypatch):
    import langsmith

    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    def forbidden(*args, **kwargs):
        raise AssertionError("A graph attempted remote tracing")

    monkeypatch.setattr(langsmith.Client, "create_run", forbidden)
    runner, bearer, query = harness()
    assert invoke(runner, bearer, query).status == "succeeded"


@pytest.mark.parametrize("field", ["code_sha256", "schemas_sha256"])
def test_graph_manifest_rejects_code_or_schema_drift(field):
    with pytest.raises(ValueError, match="graph_binding"):
        settings(**{field: "a" * 64})


def test_cli_and_graph_snapshots_validate_the_shipped_profile(capsys, tmp_path):
    from jsonschema import Draft202012Validator

    assert main(["agent-graph-check", str(CONFIG)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["config_id"] == settings().config_id and result["provider_invoked"] is False
    for name in ("graph-request", "graph-result", "graph-config", "graph-policy", "safe-trace"):
        schema = json.loads((ROOT / f"contracts/agent/v1/{name}.v1.schema.json").read_text())
        value = json.loads((ROOT / f"contracts/agent/v1/{name}.v1.example.json").read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version":"1.0","schema_version":"1.0"}')
    assert main(["agent-graph-check", str(bad)]) == 2
    assert capsys.readouterr().err == "agent_graph_config_invalid\n"


def test_subtraction_preserves_decimal_precision_across_very_different_magnitudes():
    from decimal import Decimal, localcontext

    def extreme(call, raw):
        raw["items"][0]["observed_sales_units"] = (
            1e100 if call.window.start.isoformat() == "2026-08-16" else 1e-100
        )

    query = request("sales_comparison")
    runner, bearer, query = harness(query, cases=fixture_outputs(query, change=extreme))
    result = invoke(runner, bearer, query)
    assert result.status == "succeeded"
    calculation = next(
        claim for claim in result.answer.evidence if claim.source_type == "calculation"
    )
    with localcontext() as context:
        context.prec = 700
        expected = format(Decimal("1e100") - Decimal("1e-100"), "f")
    assert f"= {expected} unit" in calculation.claim


def test_compiled_graph_is_acyclic_and_has_no_checkpointer(monkeypatch):
    from langgraph.graph import StateGraph

    original = StateGraph.compile
    captured = []

    def compile_checked(self, *args, **kwargs):
        compiled = original(self, *args, **kwargs)
        captured.append(compiled)
        assert compiled.checkpointer is False
        graph = compiled.get_graph()
        edges = {}
        for edge in graph.edges:
            edges.setdefault(edge.source, []).append(edge.target)

        def visit(node, path):
            assert node not in path
            for target in edges.get(node, []):
                visit(target, path | {node})

        visit("__start__", set())
        return compiled

    monkeypatch.setattr(StateGraph, "compile", compile_checked)
    runner, bearer, query = harness()
    assert invoke(runner, bearer, query).status == "succeeded" and captured


def test_shipped_graph_manifest_loads_and_changes_are_versioned():
    resolved = load_graph_config(CONFIG)
    assert resolved.config_id == settings().config_id
    assert settings(policy={"max_selected_facts": 4}).config_id != resolved.config_id
    assert all(prompt.resource_version == "v4" for prompt in resolved.config.chat.prompts)


@pytest.mark.parametrize(
    "change",
    [
        {"principal_id": PRIVATE},
        {"conversation_id": "foreign"},
        {"intent": "execute_sql"},
        {"comparison_window": {"start": "2026-08-16", "end": "2026-08-22"}},
        {"window": {"start": "2025-01-01", "end": "2026-08-22"}},
    ],
)
def test_graph_input_is_closed_and_bounded_before_execution(change):
    runner, bearer, query = harness()
    value = query.model_dump(mode="json") | change
    result = asyncio.run(runner.run_json(bearer, json.dumps(value)))
    assert result.error_code == "invalid_scope" and runner.provider.calls == 0
