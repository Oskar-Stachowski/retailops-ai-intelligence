import asyncio
import json
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from test_agent_tools import KnowledgeSpy, Spy, authority, example, session
from test_agent_tools import semantic_pin as semantic_pin
from test_chunks import config as config
from test_chunks import sources as sources
from test_semantic_embeddings import real_config as real_config

from retailops_ai.adapters.agent_tools import PinnedKnowledgeTool
from retailops_ai.adapters.fake_chat import FakeChatStep, ScriptedChatProvider
from retailops_ai.agent.chat import ChatFailure, ChatSession, SmokeBudget
from retailops_ai.agent.chat_config import (
    AgentChatConfig,
    load_chat_config,
    resolve_chat_config,
)
from retailops_ai.agent.chat_context import EvidenceSnapshot, tool_result_ref
from retailops_ai.agent.chat_contracts import AnswerDraft, PlanDraft, ProviderReply
from retailops_ai.agent.tools import OUTPUT
from retailops_ai.cli import main

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "agent/chat.fake.prepaid.v4.json"
QUESTION = "What evidence is available?"
PRIVATE = "private-chat-secret-marker"


def settings(**changes):
    value = json.loads(CONFIG.read_text())
    for key, change in changes.items():
        value[key] = {**value[key], **change} if isinstance(change, dict) else change
    return resolve_chat_config(AgentChatConfig.model_validate_json(json.dumps(value)))


class BoundedFixture(ScriptedChatProvider):
    """Known fixture token count for exercising budgets independently of byte bounds."""

    def input_token_bound(self, request):
        return 100


def chat(*, resolved=None, tools=None, bound=False, smoke=None, **kwargs):
    resolved = resolved or settings()
    if tools is None:
        executor, bearer = session(settings=resolved.config.tool_policy, **kwargs)
        tools = executor.open_session(bearer)
    cls = BoundedFixture if bound else ScriptedChatProvider
    provider = cls(resolved.config.model, (FakeChatStep("unconfigured", "schema"),))
    return ChatSession(tools, resolved, provider, smoke=smoke, jitter=lambda: 0.0)


def reply(body, inputs=100, outputs=20):
    return ProviderReply.model_validate_json(
        json.dumps(
            {
                "body": json.dumps(body) if not isinstance(body, str) else body,
                "usage": {"input_tokens": inputs, "output_tokens": outputs},
            }
        )
    ).model_dump_json()


def program(run, body=None, events=None, phase="plan", question=QUESTION, **usage):
    request = run.preview(phase, question)
    run.provider.steps = run.provider.steps[: run.provider.calls] + tuple(
        FakeChatStep(
            request.request_hash(), event, reply(body, **usage) if event == "reply" else None
        )
        for event in (events or ["reply"])
    )


def invoke(run, phase="plan", question=QUESTION):
    return asyncio.run(run.call(phase, question))


def failure(run, code, phase="plan", question=QUESTION):
    with pytest.raises(ChatFailure) as caught:
        invoke(run, phase, question)
    assert caught.value.code == code
    assert PRIVATE not in str(caught.value)
    assert PRIVATE not in json.dumps([asdict(item) for item in run.audit])


def limited(run, outcome="insufficient_evidence"):
    return {
        "kind": "answer",
        "outcome": outcome,
        "summary": "No sufficient authorized evidence.",
        "evidence": [],
        "recommended_actions": [],
        "confidence": "low",
        "data_freshness": EvidenceSnapshot.build(run.tools.accepted_outputs(), run.config)
        .freshness()
        .model_dump(mode="json"),
        "citations": [],
        "limitations": ["Synthetic fixture only."],
    }


def sales_chat(**kwargs):
    executor, bearer = session(Spy(), **kwargs)
    tools = executor.open_session(bearer)
    output = asyncio.run(tools.execute_json(json.dumps(example())))
    run = chat(tools=tools, bound=True)
    answer = limited(run)
    answer.update(
        outcome="answered", confidence="medium", summary="The sales tool returned a summary."
    )
    answer["evidence"] = [
        {
            "claim": "Sales summary is available.",
            "source_type": "tool",
            "source_ref": tool_result_ref(output),
            "as_of": output.as_of.isoformat(),
            "supporting_refs": [],
            "calculation_id": None,
        }
    ]
    return run, answer


def test_chat_contract_snapshots_are_valid_and_config_binding_is_reproducible():
    for name in ("chat-plan", "chat-answer", "chat-config", "provider-reply"):
        schema = json.loads((ROOT / f"contracts/agent/v1/{name}.v1.schema.json").read_text())
        value = json.loads((ROOT / f"contracts/agent/v1/{name}.v1.example.json").read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    bindings = json.loads((ROOT / "contracts/agent/v1/chat-bindings.v1.json").read_text())
    loaded = load_chat_config(CONFIG)
    assert loaded.config_id == bindings["config_id"] == settings().config_id
    assert len(loaded.prompt_texts) == 6
    assert loaded.config.embeddings.model_id == "amazon.titan-embed-text-v2:0"


def test_cli_checks_configuration_without_provider_or_network(capsys, tmp_path):
    assert main(["agent-config-check", str(CONFIG)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "valid",
        "provider": "fake",
        "config_id": settings().config_id,
        "provider_invoked": False,
    }
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**json.loads(CONFIG.read_text()), "secret": PRIVATE}))
    assert main(["agent-config-check", str(bad)]) == 2
    assert capsys.readouterr().err == "agent_chat_config_invalid\n"


@pytest.mark.parametrize(
    "change",
    [
        {"model": {"temperature": 0.2}},
        {"budget": {"max_calls": 5}},
        {"prompt_version": "prompts-v2"},
        {"knowledge_index_id": "index-sha256-" + "b" * 64},
        {"retrieval": {"min_cosine_score": 0.1}},
    ],
)
def test_every_evaluated_configuration_change_has_a_new_identity(change):
    assert settings(**change).config_id != settings().config_id


@pytest.mark.parametrize("field", ["tool_schemas_sha256", "response_schema_sha256"])
def test_schema_drift_is_rejected_before_provider_use(field):
    with pytest.raises(ValueError, match="schema_binding"):
        settings(**{field: "a" * 64})


@pytest.mark.parametrize(
    "mutation", ["checksum", "path", "order", "provider_rates", "deadline", "tokens"]
)
def test_invalid_prompt_and_budget_bindings_are_rejected(mutation):
    value = json.loads(CONFIG.read_text())
    if mutation == "checksum":
        value["prompts"][0]["sha256"] = "a" * 64
    elif mutation == "path":
        value["prompts"][0]["name"] = "../../private"
    elif mutation == "order":
        value["prompts"].reverse()
    elif mutation == "provider_rates":
        value["budget"]["pricing"]["kind"] = "reviewed_provider_rates"
    elif mutation == "deadline":
        value["tool_policy"]["request_deadline_seconds"] = 46.0
    else:
        value["budget"]["max_output_tokens"] = 3001
    with pytest.raises(ValueError):
        resolve_chat_config(AgentChatConfig.model_validate_json(json.dumps(value)))


@pytest.mark.parametrize(
    "raw", ['{"schema_version":"1.0","schema_version":"1.0"}', '{"x":NaN}', "x" * 65537]
)
def test_config_loader_rejects_ambiguous_or_oversized_json(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(raw)
    with pytest.raises(ValueError, match="^agent_chat_config_invalid$"):
        load_chat_config(path)


def test_resolved_configuration_is_detached_and_tampered_snapshot_is_rejected():
    resolved = settings()
    original = resolved.config_id
    resolved.config.embeddings.model_copy(update={"dimension": 512})
    assert resolved.config_id == original
    altered = replace(resolved, prompt_texts=(("policy", PRIVATE),) + resolved.prompt_texts[1:])
    with pytest.raises(ValueError, match="prompt_snapshot"):
        chat(resolved=altered)


def test_fake_chat_requires_explicit_test_mode_and_cannot_reset_one_run_budget():
    executor, bearer = session(allow=False)
    with pytest.raises(ValueError, match="explicit_test_mode"):
        chat(tools=executor.open_session(bearer))
    run = chat()
    with pytest.raises(ValueError, match="already_bound"):
        chat(tools=run.tools)
    executor, bearer = session(
        settings=settings().config.tool_policy.model_copy(update={"max_calls": 1})
    )
    with pytest.raises(ValueError, match="binding_mismatch"):
        chat(tools=executor.open_session(bearer))


def test_valid_plan_is_typed_scope_checked_and_never_executes_itself():
    spy = Spy()
    executor, bearer = session(spy)
    run = chat(tools=executor.open_session(bearer))
    program(run, {"kind": "tool_plan", "tools": [example()]})
    answer = invoke(run)
    assert isinstance(answer, PlanDraft) and answer.tools[0].tool == "get_sales_summary"
    assert run.calls == 1 and spy.calls == run.tools.calls == 0
    assert run.input_tokens == 100 and run.output_tokens == 20
    assert run.audit[-1].status == "ok"


@pytest.mark.parametrize(
    "body",
    [
        PRIVATE,
        '{"kind":"tool_plan","kind":"tool_plan","tools":[]}',
        '{"kind":"tool_plan","tools":[],"x":NaN}',
        {"kind": "tool_plan", "tools": [], "principal_id": PRIVATE},
        {"kind": "tool_plan", "tools": [{"tool": "execute_sql", "sql": PRIVATE}]},
        {"kind": "tool_plan", "tools": [{"tool": "change_price"}]},
        {"kind": "tool_plan", "tools": [dict(example(), url=PRIVATE)]},
        {"kind": "answer", "outcome": "answered"},
        "ą" * 70000,
    ],
)
def test_invalid_output_never_executes_tools_and_retains_only_safe_metadata(body):
    run = chat()
    program(run, body)
    failure(run, "invalid_output")
    assert run.tools.calls == 0 and run.calls == 1
    assert run.audit[-1].input_tokens == run.input_tokens == 100
    assert run.audit[-1].output_tokens == run.output_tokens == 20


@pytest.mark.parametrize(
    "mutation", ["scope", "capability", "period", "rows", "future", "missing_pin"]
)
def test_planned_calls_pass_server_authorization_before_any_execution(mutation):
    access = (
        authority(capabilities=["assistant:query", "inventory:read"])
        if mutation == "capability"
        else None
    )
    run = chat(access=access)
    value = example()
    if mutation == "scope":
        value["scope"]["selling_location_ids"] = ["foreign-store"]
    elif mutation == "period":
        value["window"]["start"] = "2025-01-01"
    elif mutation == "rows":
        value["limit"] = 51
    elif mutation == "future":
        value["as_of"] = "2026-08-24T00:00:00Z"
    elif mutation == "missing_pin":
        value = example("search_knowledge")
    program(run, {"kind": "tool_plan", "tools": [value]})
    failure(run, "invalid_output" if mutation in {"period", "rows"} else "unauthorized_tool")
    assert run.tools.calls == 0


@pytest.mark.parametrize("outcome", ["insufficient_evidence", "refused"])
def test_structured_limited_answers_are_accepted_without_business_facts(outcome):
    run = chat()
    program(run, limited(run, outcome), phase="synthesize")
    assert invoke(run, "synthesize").outcome == outcome


def test_tool_evidence_and_freshness_come_from_detached_authorized_results():
    run, answer = sales_chat()
    detached = run.tools.accepted_outputs()[0]
    detached.items.clear()
    assert run.tools.accepted_outputs()[0].items
    request = run.preview("synthesize", QUESTION)
    assert json.loads(request.references_json)["content_trust"] == "untrusted_reference"
    program(run, answer, phase="synthesize")
    result = invoke(run, "synthesize")
    assert (
        isinstance(result, AnswerDraft)
        and result.evidence[0].source_ref == answer["evidence"][0]["source_ref"]
    )


@pytest.mark.parametrize(
    "mutation", ["ref", "as_of", "support", "freshness", "citation", "action", "calculation"]
)
def test_invented_evidence_citations_and_model_actions_are_rejected(mutation):
    run, answer = sales_chat()
    claim = answer["evidence"][0]
    if mutation == "ref":
        claim["source_ref"] = PRIVATE
    elif mutation == "as_of":
        claim["as_of"] = "2026-08-22T00:00:00Z"
    elif mutation == "support":
        claim["supporting_refs"] = [PRIVATE]
    elif mutation == "freshness":
        answer["data_freshness"]["inventory"] = {"as_of": claim["as_of"], "status": "current"}
    elif mutation == "citation":
        answer["citations"] = [
            {
                "repository": "Oskar-Stachowski/retailops-ai-intelligence",
                "commit_sha": "a" * 40,
                "path": "docs/STATUS.md",
                "heading": "Unknown",
                "chunk_id": "chunk-sha256-" + "a" * 64,
                "document_status": "verified",
                "source_ref": PRIVATE,
            }
        ]
    elif mutation == "action":
        answer["recommended_actions"] = [
            {
                "action": "Change the price",
                "priority": "high",
                "rationale": "Unapproved",
                "evidence_refs": [claim["source_ref"]],
                "requires_human_review": True,
            }
        ]
    else:
        claim.update(
            source_type="calculation",
            calculation_id="unregistered-difference",
            supporting_refs=[claim["source_ref"]] * 2,
        )
    program(run, answer, phase="synthesize")
    failure(run, "invalid_evidence", "synthesize")


def test_no_data_is_distinct_from_an_available_business_fact():
    value = example(direction="result")
    value.update(status="no_data", items=[])
    value["freshness_status"] = "missing"
    spy = Spy(result=OUTPUT.validate_json(json.dumps(value)))
    executor, bearer = session(spy)
    tools = executor.open_session(bearer)
    output = asyncio.run(tools.execute_json(json.dumps(example())))
    run = chat(tools=tools)
    body = limited(run)
    assert body["data_freshness"]["sales"]["status"] == "missing"
    body.update(
        outcome="answered",
        confidence="medium",
        evidence=[
            {
                "claim": "A business fact",
                "source_type": "tool",
                "source_ref": tool_result_ref(output),
                "as_of": output.as_of.isoformat(),
                "supporting_refs": [],
            }
        ],
    )
    program(run, body, phase="synthesize")
    failure(run, "invalid_evidence", "synthesize")


def test_retrieved_citation_identity_is_preserved_and_modification_rejected(semantic_pin):
    pin, candidate = semantic_pin
    executor, bearer = session(
        PinnedKnowledgeTool(KnowledgeSpy(candidate)), tool="search_knowledge"
    )
    tools = executor.open_session(bearer, pin=pin)
    request = example("search_knowledge")
    request["retrieval"]["purpose"] = "documentation"
    asyncio.run(tools.execute_json(json.dumps(request)))
    run = chat(
        tools=tools,
        bound=True,
        resolved=settings(
            knowledge_index_id=pin.manifest.index_id,
            embeddings=pin.manifest.embedding_config.model_dump(mode="json"),
        ),
    )
    citation = next(
        iter(EvidenceSnapshot.build(tools.accepted_outputs(), run.config).citations().values())
    )
    body = limited(run)
    body.update(
        outcome="answered",
        confidence="medium",
        citations=[citation.model_dump(mode="json")],
        evidence=[
            {
                "claim": "Documentation describes a process.",
                "source_type": "document",
                "source_ref": citation.source_ref,
                "as_of": None,
                "supporting_refs": [],
            }
        ],
    )
    program(run, body, phase="synthesize")
    assert invoke(run, "synthesize").citations == [citation]
    body["citations"][0]["document_status"] = (
        "verified" if citation.document_status != "verified" else "specified"
    )
    program(run, body, phase="synthesize")
    failure(run, "invalid_evidence", "synthesize")


def test_retrieval_configuration_drift_is_rejected_before_chat(semantic_pin):
    pin, candidate = semantic_pin
    executor, bearer = session(
        PinnedKnowledgeTool(KnowledgeSpy(candidate)), tool="search_knowledge"
    )
    tools = executor.open_session(bearer, pin=pin)
    request = example("search_knowledge")
    request["retrieval"]["purpose"] = "documentation"
    asyncio.run(tools.execute_json(json.dumps(request)))
    run = chat(
        tools=tools,
        resolved=settings(
            knowledge_index_id=pin.manifest.index_id,
            embeddings=pin.manifest.embedding_config.model_dump(mode="json"),
            retrieval={"min_cosine_score": 0.1},
        ),
    )
    failure(run, "invalid_evidence", "synthesize")
    assert run.calls == run.provider.calls == 0


def test_context_cannot_change_during_synthesis_or_between_error_and_repair():
    async def scenario():
        executor, bearer = session(Spy())
        run = chat(tools=executor.open_session(bearer), bound=True)

        async def changed(request):
            output = await run.tools.execute_json(json.dumps(example()))
            body = limited(run)
            body.update(
                outcome="answered",
                evidence=[
                    {
                        "claim": "A fact added after the request",
                        "source_type": "tool",
                        "source_ref": tool_result_ref(output),
                        "as_of": output.as_of.isoformat(),
                        "supporting_refs": [],
                    }
                ],
            )
            return ProviderReply.model_validate_json(reply(body))

        run.provider.generate = changed
        with pytest.raises(ChatFailure) as caught:
            await run.call("synthesize", QUESTION)
        assert caught.value.code == "invalid_evidence"
        with pytest.raises(ChatFailure) as caught:
            await run.call("repair", QUESTION)
        assert caught.value.code == "invalid_repair" and run.calls == 1

    asyncio.run(scenario())


def test_plan_cannot_exceed_remaining_tool_budget():
    resolved = settings(tool_policy={"max_calls": 1})
    executor, bearer = session(Spy(), settings=resolved.config.tool_policy)
    tools = executor.open_session(bearer)
    asyncio.run(tools.execute_json(json.dumps(example())))
    run = chat(tools=tools, resolved=resolved, bound=True)
    program(run, {"kind": "tool_plan", "tools": [example()]})
    failure(run, "budget_exceeded")
    assert tools.calls == 1


def test_one_repair_uses_same_context_and_remaining_budget_without_echoing_bad_output():
    run = chat(bound=True)
    failure(run, "invalid_repair", "repair")
    program(run, PRIVATE)
    failure(run, "invalid_output")
    failure(run, "invalid_repair", "repair", "A different question")
    repair = run.preview("repair", QUESTION)
    assert repair.expected_kind == "tool_plan" and repair.repair_code == "invalid_output"
    assert PRIVATE not in json.dumps(repair.payload())
    program(run, {"kind": "tool_plan", "tools": []}, phase="repair")
    assert isinstance(invoke(run, "repair"), PlanDraft)
    assert run.calls == 2 and run.repairs == 1 and run.input_tokens == 200
    failure(run, "invalid_repair", "repair")


def test_failed_repair_does_not_allow_a_second_attempt():
    run = chat(bound=True)
    program(run, PRIVATE)
    failure(run, "invalid_output")
    program(run, PRIVATE, phase="repair")
    failure(run, "invalid_output", "repair")
    failure(run, "invalid_repair", "repair")
    assert run.calls == 2 and run.repairs == 1


@pytest.mark.parametrize("event", ["throttled", "transient"])
def test_retry_is_bounded_and_failed_attempts_keep_conservative_token_reservations(event):
    run = chat(
        bound=True, resolved=settings(budget={"retry_base_seconds": 0.0, "retry_max_seconds": 0.0})
    )
    program(run, {"kind": "tool_plan", "tools": []}, events=[event, event, "reply"])
    assert isinstance(invoke(run), PlanDraft)
    assert run.retries == 2 and run.calls == 3
    assert run.input_tokens == 300 and run.output_tokens == 820
    program(run, events=[event, "reply"])
    failure(run, "provider_unavailable")
    assert run.retries == 2 and run.calls == 4


@pytest.mark.parametrize("event", ["auth", "schema", "timeout"])
def test_nontransient_errors_and_timeout_are_not_retried(event):
    run = chat(bound=True, resolved=settings(budget={"provider_timeout_seconds": 0.01}))
    program(run, events=[event, "reply"])
    failure(run, "provider_unavailable")
    assert run.calls == run.provider.calls == 1 and run.retries == 0
    assert run.input_tokens == 100 and run.output_tokens == 400


def test_exact_script_does_not_accept_an_unexpected_question_or_phase():
    run = chat()
    program(run, {"kind": "tool_plan", "tools": []})
    failure(run, "provider_unavailable", question="Unexpected question")
    assert run.retries == 0


@pytest.mark.parametrize(
    "budget", [{"max_input_tokens": 99}, {"max_output_tokens": 400}, {"max_calls": 1}]
)
def test_token_and_call_limits_stop_before_invoking_provider(budget):
    run = chat(bound=True, resolved=settings(budget=budget))
    program(run, {"kind": "tool_plan", "tools": []}, outputs=400)
    if "max_input_tokens" not in budget:
        invoke(run)
    before = run.provider.calls
    failure(run, "budget_exceeded")
    assert run.provider.calls == before


def priced(**pricing):
    return settings(
        budget={
            "pricing": {
                "kind": "synthetic_test_rates",
                "currency": "USD",
                "input_per_million": "1",
                "output_per_million": "1",
                "rate_version": "fixture-priced-v1",
                "max_run_cost": "0.001",
                "max_smoke_cost": "0.002",
                **pricing,
            }
        }
    )


def test_shared_smoke_cost_cap_accounts_for_failed_calls_across_runs():
    resolved = priced(max_run_cost="0.0005", max_smoke_cost="0.0008")
    smoke = SmokeBudget(resolved.config.budget.pricing)
    first = chat(bound=True, resolved=resolved, smoke=smoke)
    program(first, events=["auth"])
    failure(first, "provider_unavailable")
    assert first.cost == smoke.charged == Decimal("0.0005")
    second = chat(bound=True, resolved=resolved, smoke=smoke)
    program(second, {"kind": "tool_plan", "tools": []})
    failure(second, "budget_exceeded")
    assert second.provider.calls == second.calls == 0
    program(first, {"kind": "tool_plan", "tools": []})
    failure(first, "budget_exceeded")


def test_actual_usage_releases_only_unused_reservation_and_mismatched_usage_keeps_full_charge():
    resolved = priced()
    run = chat(bound=True, resolved=resolved)
    program(run, {"kind": "tool_plan", "tools": []})
    invoke(run)
    assert run.cost == run.smoke.charged == Decimal("0.00012")
    program(run, {"kind": "tool_plan", "tools": []}, inputs=101)
    failure(run, "provider_unavailable")
    assert run.cost == run.smoke.charged == Decimal("0.00062")


def test_provider_exceptions_and_token_estimator_failures_do_not_leak_private_messages():
    run = chat(bound=True)

    async def broken(request):
        raise RuntimeError(PRIVATE)

    run.provider.generate = broken
    failure(run, "provider_unavailable")
    assert run.calls == 1

    def broken_bound(request):
        raise RuntimeError(PRIVATE)

    run.provider.input_token_bound = broken_bound
    failure(run, "provider_unavailable")
    assert run.calls == 1


def test_model_and_tools_share_the_same_deadline_and_late_reply_is_discarded():
    clock = [0.0]
    run = chat(bound=True, timer=lambda: clock[0])
    program(run, {"kind": "tool_plan", "tools": []})
    clock[0] = 45.0
    failure(run, "deadline_exceeded")
    assert run.provider.calls == 0
    clock[0] = 44.0
    original = run.provider.generate

    async def late(request):
        value = await original(request)
        clock[0] = 46.0
        return value

    run.provider.generate = late
    failure(run, "deadline_exceeded")
    assert run.calls == 1


def test_concurrent_model_calls_share_budget_and_cancellation_does_not_refund_unknown_usage():
    async def scenario():
        run = chat(bound=True, resolved=settings(budget={"max_calls": 2}))
        program(run, {"kind": "tool_plan", "tools": []}, events=["reply", "reply", "reply"])
        results = await asyncio.gather(
            *(run.call("plan", QUESTION) for _ in range(3)), return_exceptions=True
        )
        assert sum(isinstance(result, PlanDraft) for result in results) == 2
        assert isinstance(results[-1], ChatFailure) and results[-1].code == "budget_exceeded"
        assert run.provider.calls == 2
        cancelled = chat(bound=True, resolved=settings(budget={"max_calls": 1}))
        program(cancelled, events=["timeout"])
        task = asyncio.create_task(cancelled.call("plan", QUESTION))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.calls == 1 and cancelled.output_tokens == 400
        assert cancelled.audit[-1].error_code == "cancelled"
        with pytest.raises(ChatFailure, match="budget"):
            await cancelled.call("plan", QUESTION)

    asyncio.run(scenario())
