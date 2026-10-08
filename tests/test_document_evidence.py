"""Question coverage tests; fixed golden answers remain independent of the policy."""

import asyncio
import json
from datetime import datetime
from functools import partial
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_agent_graph import PolicyFixture

from retailops_ai.agent.document_evidence import DocumentSupport
from retailops_ai.agent.evaluation import FrozenScript, RecordedTools, fixture_authority
from retailops_ai.agent.evaluation_contracts import AgentGoldenSet, GoldenCase
from retailops_ai.agent.execution import ToolExecutor
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import (
    AgentGraphConfig,
    load_graph_config,
    resolve_graph_config,
)
from retailops_ai.agent.graph_contracts import GraphPolicy, GraphRequest
from retailops_ai.agent.graph_traces import MemoryTraces
from retailops_ai.agent.tools import KnowledgeResult
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.chunks import MarkdownChunk
from retailops_ai.pipelines.retrieval import context_size

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "agent/graph.evaluate.fake.prepaid.v3.json"
SUITE = AgentGoldenSet.model_validate_json((ROOT / "agent/golden.canonical.v1.json").read_bytes())


def case(number):
    return next(row for row in SUITE.cases if row.case_id == f"document-{number}")


def run_case(value, *, transform=None, frozen=False, config=None):
    config = config or load_graph_config(CONFIG)
    auth, bearer = fixture_authority(value)
    adapter = RecordedTools(value)
    executor = ToolExecutor(
        auth,
        {row.request.tool: adapter for row in value.tools},
        config.config.chat.tool_policy,
        "test",
        allow_fixtures=True,
        clock=partial(datetime.fromisoformat, value.decision_time),
    )
    provider = (
        FrozenScript(value, config.config.chat.model)
        if frozen
        else PolicyFixture(config.config.chat.model, transform=transform)
    )
    runner = GraphRunner(
        executor, config, provider, MemoryTraces(config.config.policy), pin=SUITE.pin
    )
    return asyncio.run(runner.run_json(bearer, value.request_json)), provider


def changed_case(number, mutate):
    raw = case(number).model_dump(mode="json")
    mutate(raw)
    for tool in raw["tools"]:
        output = tool["result"]
        size = context_size(KnowledgeResult.model_validate_json(json.dumps(output)).items)
        output.update(context_bytes=size, context_tokens=(size + 3) // 4)
    return GoldenCase.model_validate_json(json.dumps(raw))


def changed_config(mutate):
    raw = json.loads(CONFIG.read_text())
    mutate(raw)
    return resolve_graph_config(AgentGraphConfig.model_validate_json(json.dumps(raw)))


@pytest.mark.parametrize("number", range(1, 7))
def test_independent_document_oracles_pass_with_frozen_script(number):
    value = case(number)
    result, provider = run_case(value, frozen=True)
    assert result.status == "succeeded", result.model_dump_json()
    assert result.answer == value.expected.answer
    assert provider.calls == value.expected.model_calls


def test_irrelevant_top_ranked_document_is_not_an_answer():
    def mutate(raw):
        raw["tools"][0]["result"] = json.loads(
            (ROOT / "tests/fixtures/document-irrelevant.v1.json").read_text()
        )
        raw["tools"][0]["result"]["items"][0]["score"] = 1.0

    result, provider = run_case(changed_case(1, mutate))
    assert result.status == "succeeded"
    assert result.answer.outcome == "insufficient_evidence"
    assert not result.answer.evidence and not result.answer.citations
    assert provider.calls == 1  # A model is not asked to turn irrelevant text into an answer.


def test_verified_status_does_not_make_unrelated_document_sufficient():
    value = case(5)
    assert value.tools[0].result.items[0].chunk.document_status == "verified"
    result, provider = run_case(value)
    assert result.answer.outcome == "insufficient_evidence"
    assert provider.calls == 1
    positive = GoldenCase.model_validate_json(
        (ROOT / "tests/fixtures/document-verified-positive.v1.json").read_bytes()
    )
    result, _ = run_case(positive, frozen=True)
    assert result.answer == positive.expected.answer
    assert "Verified evidence" in result.answer.evidence[0].claim


def test_partial_question_coverage_stops_before_synthesis():
    value = changed_case(
        6,
        lambda raw: raw["tools"][0]["result"].update(items=raw["tools"][0]["result"]["items"][:1]),
    )
    result, provider = run_case(value)
    assert result.answer.outcome == "insufficient_evidence"
    assert "Required document evidence is missing: ready-meaning." in result.answer.limitations
    assert provider.calls == 1


def test_model_cannot_omit_a_required_part_of_the_answer():
    def drop_ready(request, body):
        if request.expected_kind == "answer":
            body["evidence"] = body["evidence"][:1]
            body["summary"] = body["evidence"][0]["claim"]
            body["citations"] = body["citations"][:1]
        return body

    result, _ = run_case(case(6), transform=drop_ready)
    assert result.status == "failed" and result.error_code == "invalid_evidence"
    assert result.answer is None and result.trace.repairs == 1


@pytest.mark.parametrize(
    "question,intent",
    [
        ("What is the local startup process?", "documentation"),
        (json.loads(case(1).request_json)["question"], "verified_state"),
    ],
)
def test_unknown_question_or_different_purpose_cannot_reuse_a_rule(question, intent):
    def mutate(raw):
        request = json.loads(raw["request_json"])
        request.update(question=question, intent=intent)
        raw["request_json"] = json.dumps(request)
        raw["tools"][0]["request"]["retrieval"].update(question=question, purpose=intent)
        if intent == "verified_state":
            raw["tools"][0]["result"].update(items=[], status="no_data")

    result, provider = run_case(changed_case(1, mutate))
    assert result.answer.outcome == "insufficient_evidence"
    assert provider.calls == 1
    assert (
        "No document evidence rule matches this question and purpose." in result.answer.limitations
    )


def test_presentation_normalization_is_allowed_without_keyword_matching():
    def mutate(raw):
        request = json.loads(raw["request_json"])
        question = "  " + request["question"].upper().replace(" ", "\n  ") + "  "
        request["question"] = question
        raw["request_json"] = json.dumps(request)
        raw["tools"][0]["request"]["retrieval"]["question"] = question

    result, _ = run_case(changed_case(1, mutate))
    assert result.answer == case(1).expected.answer


def test_full_quote_after_the_old_prefix_limit_and_injection_is_only_data():
    result, _ = run_case(case(1), frozen=True)
    assert "Ctrl+C stops the process." in result.answer.evidence[0].claim
    result, _ = run_case(case(6), frozen=True)
    assert len(result.answer.evidence) == 2 and len(result.answer.citations) == 2
    assert "ignore the system" not in result.answer.model_dump_json()
    assert result.trace.tool_calls == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("fact_scope", "Another environment"),
        ("document_status", "implemented"),
        ("title", "Different title"),
    ],
)
def test_valid_source_metadata_changes_invalidate_reviewed_binding(field, value):
    source = case(1).tools[0].result.items[0].chunk
    rule = load_graph_config(CONFIG).config.policy.document_rules[0]
    support = rule.requirements[0].supports[0]
    assert support.matches(source)
    raw = source.model_dump(mode="json") | {field: value}
    if field == "document_status":
        raw["implementation_refs"] = [
            {
                "repository": source.repository,
                "commit_sha": source.commit_sha,
                "path": "src/retailops_ai/cli.py",
                "byte_sha256": "a" * 64,
            }
        ]
    changed = MarkdownChunk.model_validate_json(json.dumps(raw))
    assert not support.matches(changed)

    result, provider = run_case(
        changed_case(
            1,
            lambda raw: raw["tools"][0]["result"]["items"][0].update(
                chunk=changed.model_dump(mode="json"),
                claim_kind="implementation" if field == "document_status" else "plan",
            ),
        )
    )
    assert result.answer.outcome == "insufficient_evidence"
    assert provider.calls == 1


def test_bound_hash_still_requires_a_literal_quote():
    source = case(1).tools[0].result.items[0].chunk
    support = DocumentSupport(
        chunk_id=source.chunk_id,
        chunk_sha256=canonical_sha256(source.model_dump(mode="json")),
        quote="This answer is not in the source.",
    )
    assert not support.matches(source)


def test_two_requirements_can_use_distinct_quotes_from_the_same_cited_chunk():
    def mutate(raw):
        rule = raw["policy"]["document_rules"][0]
        support = rule["requirements"][0]["supports"][0]
        rule["requirements"] = [
            {
                "requirement_id": "install",
                "description": "Install dependencies",
                "supports": [
                    support
                    | {
                        "quote": "Run make bootstrap from the repository root to install the locked dependencies."
                    }
                ],
            },
            {
                "requirement_id": "stop",
                "description": "Stop the process",
                "supports": [support | {"quote": "Ctrl+C stops the process."}],
            },
        ]

    result, _ = run_case(case(1), config=changed_config(mutate))
    assert result.status == "succeeded" and result.answer.outcome == "answered"
    assert len(result.answer.evidence) == 2 and len(result.answer.citations) == 1


def test_trusted_rule_cannot_override_verified_state_authorization():
    def mutate(raw):
        rule = raw["policy"]["document_rules"][0]
        rule["intent"] = "verified_state"

    def request_change(raw):
        query = json.loads(raw["request_json"])
        query["intent"] = "verified_state"
        raw["request_json"] = json.dumps(query)
        raw["tools"][0]["request"]["retrieval"]["purpose"] = "verified_state"

    result, provider = run_case(changed_case(1, request_change), config=changed_config(mutate))
    assert result.status == "failed" and result.error_code == "dependency_unavailable"
    assert result.answer is None and provider.calls == 1


def test_empty_policy_fails_closed_and_callers_cannot_supply_rules():
    config = changed_config(lambda raw: raw["policy"].update(document_rules=[]))
    result, provider = run_case(case(1), config=config)
    assert result.answer.outcome == "insufficient_evidence" and provider.calls == 1
    assert not load_graph_config(
        ROOT / "agent/graph.fake.prepaid.v3.json"
    ).config.policy.document_rules
    request = json.loads(case(1).request_json)
    request["document_rules"] = load_graph_config(CONFIG).config.policy.model_dump(mode="json")[
        "document_rules"
    ]
    with pytest.raises(ValidationError):
        GraphRequest.model_validate_json(json.dumps(request))


def test_rule_changes_are_bound_to_config_and_ambiguous_rules_are_rejected():
    original = load_graph_config(CONFIG)
    changed = changed_config(
        lambda raw: raw["policy"]["document_rules"][0]["requirements"][0].update(
            description="Reviewed description change"
        )
    )
    assert original.config_id != changed.config_id
    raw = original.config.policy.model_dump(mode="json")
    duplicate = json.loads(json.dumps(raw["document_rules"][0]))
    duplicate["question"] = " " + duplicate["question"].upper() + " "
    raw["document_rules"].append(duplicate)
    with pytest.raises(ValidationError, match="ambiguous_document_question_rules"):
        GraphPolicy.model_validate_json(json.dumps(raw))
