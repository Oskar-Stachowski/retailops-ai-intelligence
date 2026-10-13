"""A frozen small real-chat smoke over explicitly synthetic tool/retrieval inputs."""

from datetime import UTC, datetime
from functools import partial
from typing import Annotated, cast

from pydantic import Field, field_validator

from retailops_ai.adapters.bedrock_chat import TRANSPORT_VERSION, BedrockChatProvider, CircuitPolicy
from retailops_ai.agent.chat import ChatProvider, SmokeBudget, estimated_cost
from retailops_ai.agent.evaluation import (
    RecordedTools,
    fixture_authority,
    schema_pass,
    verify_binding,
)
from retailops_ai.agent.evaluation_contracts import AgentEvaluationRelease, AgentGoldenSet
from retailops_ai.agent.execution import ToolAdapter, ToolExecutor
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import ResolvedGraphConfig
from retailops_ai.agent.graph_traces import MemoryTraces
from retailops_ai.data_contracts.common import Symbol, Versioned
from retailops_ai.data_contracts.identity import canonical_sha256


class BedrockSmokeProfile(Versioned):
    offline_release_id: Annotated[
        str, Field(pattern=r"^agent-evaluation-release-sha256-[0-9a-f]{64}$")
    ]
    runtime_graph_config_id: Annotated[
        str, Field(pattern=r"^agent-graph-config-sha256-[0-9a-f]{64}$")
    ]
    case_ids: Annotated[tuple[Symbol, ...], Field(min_length=5, max_length=10)]
    circuit: CircuitPolicy

    @field_validator("case_ids", mode="before")
    @classmethod
    def cases(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    def profile_id(self) -> str:
        return "bedrock-smoke-profile-sha256-" + canonical_sha256(self.model_dump(mode="json"))


def verify_smoke(
    profile: BedrockSmokeProfile,
    suite: AgentGoldenSet,
    release: AgentEvaluationRelease,
    offline: ResolvedGraphConfig,
    runtime: ResolvedGraphConfig,
) -> None:
    verify_binding(suite, release, offline)
    runtime.verified()
    expected = offline.config.model_dump(mode="json")
    actual = runtime.config.model_dump(mode="json")
    # Only the evaluated provider/model/pricing/timeout profile may differ.
    for key in ("model", "budget"):
        expected["chat"][key] = actual["chat"][key]
    case_ids = {case.case_id for case in suite.cases}
    if (
        expected != actual
        or runtime.config.chat.model.provider != "bedrock"
        or profile.offline_release_id != release.release_id
        or profile.runtime_graph_config_id != runtime.config_id
        or len(set(profile.case_ids)) != len(profile.case_ids)
        or not set(profile.case_ids) <= case_ids
    ):
        raise ValueError("bedrock_smoke_binding_invalid")


def proposal(profile: BedrockSmokeProfile, runtime: ResolvedGraphConfig) -> dict[str, object]:
    config = runtime.config.chat
    maximum = estimated_cost(
        config.budget.pricing, config.budget.max_input_tokens, config.budget.max_output_tokens
    )
    return {
        "schema_version": "1.0",
        "status": "not_run",
        "reason": "explicit_cost_limit_required",
        "aws_executed": False,
        "real_chat": False,
        "data_sources": "frozen_fixtures",
        "real_retrieval_measured": False,
        "ai12_closed": False,
        "profile_id": profile.profile_id(),
        "offline_release_id": profile.offline_release_id,
        "runtime_graph_config_id": runtime.config_id,
        "transport_version": TRANSPORT_VERSION,
        "model": config.model.model_dump(mode="json"),
        "pricing": config.budget.pricing.model_dump(mode="json"),
        "case_ids": list(profile.case_ids),
        "max_calls": len(profile.case_ids) * config.budget.max_calls,
        "worst_case_estimated_usd": str(
            min(maximum * len(profile.case_ids), config.budget.pricing.max_smoke_cost)
        ),
        "scope": "real_chat_over_fixture_evidence_only",
    }


async def run_smoke(
    profile: BedrockSmokeProfile,
    suite: AgentGoldenSet,
    runtime: ResolvedGraphConfig,
    provider: BedrockChatProvider,
) -> dict[str, object]:
    if provider.model != runtime.config.chat.model or provider.breaker.policy != profile.circuit:
        raise ValueError("bedrock_smoke_provider_binding_invalid")
    smoke = SmokeBudget(runtime.config.chat.budget.pricing)
    results: list[dict[str, object]] = []
    for case_id in profile.case_ids:
        case = next(case for case in suite.cases if case.case_id == case_id)
        access, bearer = fixture_authority(case)
        adapter = RecordedTools(case)
        executor = ToolExecutor(
            access,
            {item.request.tool: cast(ToolAdapter, adapter) for item in case.tools},
            runtime.config.chat.tool_policy,
            "test",
            allow_fixtures=True,
            clock=partial(datetime.fromisoformat, case.decision_time),
        )
        runner = GraphRunner(
            executor,
            runtime,
            cast(ChatProvider, provider),
            MemoryTraces(runtime.config.policy),
            pin=suite.pin,
            smoke=smoke,
        )
        result = await runner.run_json(bearer, case.request_json)
        expected = case.expected
        checks = {
            "schema": schema_pass(result),
            "outcome": result.status == expected.status
            and result.error_code == expected.error_code
            and (result.answer.outcome if result.answer else None)
            == (expected.answer.outcome if expected.answer else None),
            "answer": result.answer == expected.answer,
            "tools": adapter.seen == expected.tool_calls,
            "action_policy": [item.recommendation_type for item in result.suggestions]
            == expected.candidate_types,
        }
        results.append(
            {
                "case_id": case_id,
                "critical": case.critical,
                "passed": all(checks.values()),
                "checks": checks,
                "status": result.status,
                "error_code": result.error_code,
                "outcome": result.answer.outcome if result.answer else None,
                "answer": result.answer.model_dump(mode="json") if result.answer else None,
                "citations": len(result.answer.citations) if result.answer else 0,
                "trace": result.trace.model_dump(mode="json") if result.trace else None,
            }
        )
        if result.error_code in {"budget_exceeded", "provider_unavailable", "deadline_exceeded"}:
            # A failed dependency/budget does not start the remaining paid cases.
            break
    report = proposal(profile, runtime)
    report.update(
        {
            "status": "passed"
            if len(results) == len(profile.case_ids) and all(item["passed"] for item in results)
            else "failed",
            "reason": None,
            "aws_executed": provider.count_requests > 0 or provider.inference_requests > 0,
            "real_chat": provider.inference_requests > 0,
            "created_at": datetime.now(UTC).isoformat(),
            "count_requests": provider.count_requests,
            "inference_requests": provider.inference_requests,
            "estimated_or_reserved_usd": str(smoke.charged),
            "cases": results,
            "cases_passed": sum(bool(item["passed"]) for item in results),
            "cases_total": len(profile.case_ids),
            "circuit": provider.breaker.state(),
            "verified_destination_regions": list(provider.destination_regions),
            "provider_diagnostics": provider.diagnostics(),
        }
    )
    return report
