"""Execute frozen scripts through the real graph and compare independently authored labels."""

import asyncio
import hashlib
import json
import math
import secrets
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from time import monotonic
from typing import Literal, cast

from retailops_ai.adapters.agent_tools import FixtureTools
from retailops_ai.agent.chat import ChatProvider, ChatRequest, ProviderFailure
from retailops_ai.agent.chat_config import ChatModelConfig
from retailops_ai.agent.chat_contracts import ProviderReply
from retailops_ai.agent.evaluation_contracts import (
    AgentEvaluationRelease,
    AgentEvaluationReport,
    AgentGoldenSet,
    CaseEvaluation,
    GoldenCase,
    Rate,
)
from retailops_ai.agent.execution import READ_CAPABILITIES, ToolAdapter, ToolExecutor
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import ResolvedGraphConfig
from retailops_ai.agent.graph_contracts import GraphResult
from retailops_ai.agent.graph_traces import MemoryTraces
from retailops_ai.agent.tools import ToolInput, ToolOutput
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.contracts import REPOSITORIES
from retailops_ai.knowledge.golden import GoldenSet
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.security.local import LocalAccess, strict_json, token_fingerprint
from retailops_ai.security.models import AccessPolicy


def evaluator_checksum() -> str:
    # Bind the evaluator's application-code dependencies too. Traverse package
    # resources so source checkouts and installed wheels have the same identity.
    digests: dict[str, str] = {}

    def collect(directory: Traversable, prefix: str) -> None:
        for resource in sorted(directory.iterdir(), key=lambda item: item.name):
            name = prefix + resource.name
            if resource.is_dir():
                collect(resource, name + "/")
            elif resource.name.endswith(".py"):
                digests[name] = hashlib.sha256(resource.read_bytes()).hexdigest()

    collect(files("retailops_ai"), "")
    return canonical_sha256(digests)


def load_evaluation(
    path: Path, release_path: Path, config: ResolvedGraphConfig, rag_path: Path, lock_path: Path
) -> tuple[AgentGoldenSet, AgentEvaluationRelease]:
    try:
        with path.open("rb") as source:
            raw = source.read(8_000_001)
        with release_path.open("rb") as source:
            release_raw = source.read(16385)
        if len(raw) > 8_000_000 or len(release_raw) > 16384:
            raise ValueError("evaluation_file_too_large")
        strict_json(raw)
        strict_json(release_raw)
        suite = AgentGoldenSet.model_validate_json(raw)
        release = AgentEvaluationRelease.model_validate_json(release_raw)
        with rag_path.open("rb") as source:
            rag_raw = source.read(2_000_001)
        with lock_path.open("rb") as source:
            lock_raw = source.read(2_000_001)
        if len(rag_raw) > 2_000_000 or len(lock_raw) > 2_000_000:
            raise ValueError("evaluation_file_too_large")
        strict_json(rag_raw)
        rag = GoldenSet.model_validate_json(rag_raw)
        questions = {case.case_id: case.request.question for case in rag.cases}
        if (
            hashlib.sha256(rag_raw).hexdigest() != suite.rag_set_sha256
            or hashlib.sha256(lock_raw).hexdigest() != release.dependency_lock_sha256
            or sum(case.rag_case_id is not None for case in suite.cases) < 6
        ):
            raise ValueError("evaluation_source_binding_invalid")
        for case in suite.cases:
            if case.rag_case_id is not None:
                strict_json(case.request_json.encode())
                request = json.loads(case.request_json)
                if not isinstance(request, dict) or questions.get(case.rag_case_id) != request.get(
                    "question"
                ):
                    raise ValueError("evaluation_question_binding_invalid")
        verify_binding(suite, release, config)
        return suite, release
    except (OSError, ValueError, RecursionError):
        raise ValueError("agent_evaluation_binding_invalid") from None


def verify_binding(
    suite: AgentGoldenSet, release: AgentEvaluationRelease, config: ResolvedGraphConfig
) -> None:
    config.verified()
    AgentGoldenSet.model_validate_json(suite.model_dump_json())
    AgentEvaluationRelease.model_validate_json(release.model_dump_json())
    if (
        config.config.chat.model.provider != "fake"
        or release.graph_config_id != config.config_id
        or release.golden_sha256 != canonical_sha256(suite.model_dump(mode="json"))
        or release.evaluator_sha256 != evaluator_checksum()
        or suite.pin.environment != "test"
        or suite.pin.manifest.index_id != config.config.chat.knowledge_index_id
        or suite.pin.manifest.embedding_config != config.config.chat.embeddings
    ):
        raise ValueError("agent_evaluation_binding_invalid")


class FrozenScript:
    """No echo of the server catalogue, no model decisions and no network."""

    source_kind: Literal["fixture"] = "fixture"

    def __init__(self, case: GoldenCase, model: ChatModelConfig) -> None:
        self.steps = tuple(case.script)
        self.calls = 0
        self.model = model

    def input_token_bound(self, request: ChatRequest) -> int:
        return 100

    async def generate(self, request: ChatRequest) -> ProviderReply:
        position = self.calls
        self.calls += 1
        if position >= len(self.steps):
            raise ProviderFailure("schema")
        step = self.steps[position]
        if step.expected_kind != request.expected_kind:
            raise ProviderFailure("schema")
        if step.event != "reply":
            raise ProviderFailure(step.event)
        return ProviderReply.model_validate_json(
            json.dumps(
                {
                    "body": step.body,
                    "usage": {"input_tokens": 100, "output_tokens": 20},
                }
            )
        )


class RecordedTools(FixtureTools):
    def __init__(self, case: GoldenCase) -> None:
        super().__init__([(item.request, item.result) for item in case.tools])
        self.seen: list[ToolInput] = []

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> ToolOutput:
        self.seen.append(request)
        return await super().execute(request, principal, pin)


def fixture_authority(case: GoldenCase) -> tuple[LocalAccess, str | None]:
    now = datetime.fromisoformat(case.decision_time)
    token = secrets.token_urlsafe(32)
    capabilities = ["assistant:query", *READ_CAPABILITIES.values()]
    if case.access == "no_source_capability":
        capabilities.remove("sales:read")
    if case.access == "viewer":
        capabilities.remove("assistant:query")
    grant = {
        "principal_id": "agent-evaluation-fixture-operator",
        "roles": ["viewer" if case.access == "viewer" else "operator"],
        "capabilities": capabilities,
        "scope": {
            "product_ids": ["p-foreign" if case.access == "foreign_scope" else "p-101"],
            "selling_location_ids": ["s-03"],
            "channels": ["store"],
        },
        "stockout_scope": {"product_ids": ["p-101"], "stock_location_ids": ["fixture-stock-01"]},
        "knowledge_scope": {
            "environment": "test",
            "repositories": list(REPOSITORIES),
            "access_classes": ["public_project"],
            "document_statuses": ["specified", "implemented", "verified"],
        },
    }
    policy = AccessPolicy.model_validate_json(
        json.dumps(
            {
                "schema_version": "1.0",
                "policy_id": "agent-evaluation-fixture-policy",
                "grants": [grant],
                "credentials": [
                    {
                        "principal_id": grant["principal_id"],
                        "token_sha256": token_fingerprint(token),
                        "not_before": (now - timedelta(minutes=1)).isoformat(),
                        "expires_at": (now + timedelta(minutes=1)).isoformat(),
                        "revoked": False,
                    }
                ],
            }
        )
    )
    return LocalAccess(policy), None if case.access == "missing_credentials" else "Bearer " + token


def ratio(passed: int, total: int) -> Rate:
    return Rate(passed=passed, total=total, value=passed / total if total else None)


def schema_pass(result: GraphResult) -> bool:
    try:
        return GraphResult.model_validate_json(result.model_dump_json()) == result
    except ValueError:
        return False


async def evaluate(
    suite: AgentGoldenSet, release: AgentEvaluationRelease, config: ResolvedGraphConfig
) -> AgentEvaluationReport:
    verify_binding(suite, release, config)
    totals: Counter[str] = Counter()
    passes: Counter[str] = Counter()
    reports: list[CaseEvaluation] = []
    unnecessary = 0
    total_cost = Decimal(0)
    for case in suite.cases:
        auth, bearer = fixture_authority(case)
        adapter = RecordedTools(case)
        provider = FrozenScript(case, config.config.chat.model)
        executor = ToolExecutor(
            auth,
            {item.request.tool: cast(ToolAdapter, adapter) for item in case.tools},
            config.config.chat.tool_policy,
            "test",
            allow_fixtures=True,
            clock=partial(datetime.fromisoformat, case.decision_time),
        )
        runner = GraphRunner(
            executor,
            config,
            cast(ChatProvider, provider),
            MemoryTraces(config.config.policy),
            pin=suite.pin,
        )
        started = monotonic()
        result = await runner.run_json(bearer, case.request_json)
        duration = (monotonic() - started) * 1000
        expected = case.expected
        trace = result.trace
        checks = {
            "outcome": result.status == expected.status
            and result.error_code == expected.error_code
            and (result.answer.outcome if result.answer else None)
            == (expected.answer.outcome if expected.answer else None),
            "tool_selection_arguments": [call.model_dump(mode="json") for call in adapter.seen]
            == [call.model_dump(mode="json") for call in expected.tool_calls],
            "call_budget": provider.calls == expected.model_calls
            and (trace.tool_calls if trace else 0) == expected.tool_attempts
            and (trace.extra_evidence_rounds if trace else 0) == expected.extra_evidence_rounds
            and (trace.repairs if trace else 0) == expected.repairs,
            "schema_pass": schema_pass(result),
            "answer_exact": result.answer == expected.answer,
            "action_policy": (result.answer.recommended_actions if result.answer else [])
            == (expected.answer.recommended_actions if expected.answer else [])
            and [row.recommendation_type for row in result.suggestions] == expected.candidate_types,
        }
        actual_ids = Counter(
            canonical_sha256(call.model_dump(mode="json")) for call in adapter.seen
        )
        expected_ids = Counter(
            canonical_sha256(call.model_dump(mode="json")) for call in expected.tool_calls
        )
        unnecessary += sum((actual_ids - expected_ids).values())
        for name, passed in checks.items():
            totals[name] += 1
            passes[name] += int(passed)
        if expected.answer is not None and result.answer is not None:
            actual = result.answer
            oracle = expected.answer
            for metric, selected, wanted in (
                ("groundedness", actual.evidence, oracle.evidence),
                ("citation_correctness", actual.citations, oracle.citations),
                (
                    "numeric_faithfulness",
                    [row for row in actual.evidence if row.source_type != "document"],
                    [row for row in oracle.evidence if row.source_type != "document"],
                ),
            ):
                # Multisets count extras, omissions and repeated claims; no vacuous 0/0 success.
                selected_ids = Counter(
                    canonical_sha256(row.model_dump(mode="json")) for row in selected
                )
                wanted_ids = Counter(
                    canonical_sha256(row.model_dump(mode="json")) for row in wanted
                )
                totals[metric] += sum((selected_ids | wanted_ids).values())
                passes[metric] += sum((selected_ids & wanted_ids).values())
            document_refs = {
                row.source_ref for row in actual.evidence if row.source_type == "document"
            }
            citation_refs = {row.source_ref for row in actual.citations}
            expected_document_refs = {
                row.source_ref for row in oracle.evidence if row.source_type == "document"
            }
            totals["citation_coverage"] += len(document_refs | expected_document_refs)
            passes["citation_coverage"] += len(
                document_refs & citation_refs & expected_document_refs
            )
        elif expected.answer is not None:
            for metric, total in (
                ("groundedness", len(expected.answer.evidence)),
                ("citation_correctness", len(expected.answer.citations)),
                ("citation_coverage", len(expected.answer.citations)),
                (
                    "numeric_faithfulness",
                    sum(row.source_type != "document" for row in expected.answer.evidence),
                ),
            ):
                totals[metric] += total
        if case.category == "safety":
            totals["safety_outcome"] += 1
            passes["safety_outcome"] += int(
                checks["outcome"] and checks["tool_selection_arguments"] and checks["action_policy"]
            )
        expected_refused = expected.answer is not None and expected.answer.outcome == "refused"
        actual_refused = result.answer is not None and result.answer.outcome == "refused"
        if expected_refused or actual_refused:
            totals["refusal_correctness"] += 1
            passes["refusal_correctness"] += int(
                expected_refused and actual_refused and checks["tool_selection_arguments"]
            )
        failed = sorted(name for name, passed in checks.items() if not passed)
        if case.critical:
            totals["critical_cases"] += 1
            passes["critical_cases"] += int(not failed)
        totals["case_pass"] += 1
        passes["case_pass"] += int(not failed)
        cost = Decimal(trace.estimated_cost) if trace else Decimal(0)
        total_cost += cost
        reports.append(
            CaseEvaluation(
                case_id=case.case_id,
                critical=case.critical,
                passed=not failed,
                failed_checks=failed,
                duration_ms=duration,
                tool_calls=len(adapter.seen),
                model_calls=provider.calls,
                input_tokens=trace.input_tokens if trace else 0,
                output_tokens=trace.output_tokens if trace else 0,
                estimated_cost=str(cost),
            )
        )
    metrics = {name: ratio(passes[name], totals[name]) for name in sorted(totals)}
    p95 = sorted(row.duration_ms for row in reports)[math.ceil(len(reports) * 0.95) - 1]
    failed_gates = [name for name, rate in metrics.items() if rate.total == 0 or rate.value != 1.0]
    if unnecessary:
        failed_gates.append("unnecessary_tool_calls")
    if p95 > suite.thresholds.latency_p95_ms_max:
        failed_gates.append("latency_p95")
    if total_cost != Decimal(suite.thresholds.estimated_cost_usd_max):
        failed_gates.append("estimated_cost")
    return AgentEvaluationReport(
        schema_version="1.0",
        status="failed" if failed_gates else "passed",
        release_id=release.release_id,
        graph_config_id=config.config_id,
        golden_sha256=release.golden_sha256,
        evaluator_sha256=release.evaluator_sha256,
        provider="fake",
        fixture_only=True,
        aws_executed=False,
        rag_quality_remeasured=False,
        labels_state="proposed",
        acceptance_scope="canonical_fixture_invariants_only",
        metrics=metrics,
        unnecessary_tool_calls=unnecessary,
        latency_p95_ms=p95,
        input_tokens=sum(row.input_tokens for row in reports),
        output_tokens=sum(row.output_tokens for row in reports),
        estimated_cost_usd=str(total_cost),
        failed_gates=sorted(failed_gates),
        cases=reports,
    )


def evaluate_sync(
    suite: AgentGoldenSet, release: AgentEvaluationRelease, config: ResolvedGraphConfig
) -> AgentEvaluationReport:
    # Suite cap is separate from each run's hard deadline; no open evaluation loop.
    return asyncio.run(asyncio.wait_for(evaluate(suite, release, config), timeout=120))
