"""Immutable model/prompt/schema bindings, loaded without provider/network access."""

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from retailops_ai.agent.chat_contracts import AnswerDraft, PlanDraft
from retailops_ai.agent.tools import REQUEST_MODELS, RESULT_MODELS, ToolPolicy
from retailops_ai.data_contracts.common import Contract, Sha256, Symbol, Versioned
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.indexes import EmbeddingConfig, IndexID
from retailops_ai.knowledge.retrieval import RetrievalConfig
from retailops_ai.security.local import strict_json

PromptName = Literal["policy", "tool_selection", "evidence", "response", "refusal", "examples"]
PROMPT_NAMES: tuple[PromptName, ...] = (
    "policy",
    "tool_selection",
    "evidence",
    "response",
    "refusal",
    "examples",
)
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=9)]


class ChatModelConfig(Contract):
    provider: Literal["fake", "bedrock"]
    model_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")]
    inference_profile: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,299}$")] | None
    region: Annotated[str, Field(pattern=r"^(offline|[a-z]{2}-[a-z]+-[0-9])$")]
    temperature: Annotated[float, Field(ge=0, le=1)]
    max_output_tokens: Annotated[int, Field(ge=1, le=1500)]

    @model_validator(mode="after")
    def provider_binding(self) -> Self:
        if self.provider == "fake":
            if (
                self.model_id != "scripted-chat-v1"
                or self.region != "offline"
                or self.inference_profile is not None
            ):
                raise ValueError("fake_chat_binding_mismatch")
        elif self.region == "offline":
            raise ValueError("bedrock_chat_requires_region")
        return self


class ChatPricing(Contract):
    kind: Literal["synthetic_test_rates", "reviewed_provider_rates"]
    currency: Literal["USD"]
    input_per_million: Money
    output_per_million: Money
    rate_version: Symbol
    max_run_cost: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=9)]
    max_smoke_cost: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=9)]

    @model_validator(mode="after")
    def caps(self) -> Self:
        if self.max_run_cost > self.max_smoke_cost:
            raise ValueError("run_cost_exceeds_smoke_cap")
        return self


class ModelBudget(Contract):
    max_input_tokens: Annotated[int, Field(ge=1, le=12000)]
    max_output_tokens: Annotated[int, Field(ge=1, le=1500)]
    max_calls: Annotated[int, Field(ge=1, le=6)]
    max_retries: Annotated[int, Field(ge=0, le=2)]
    max_repairs: Literal[1]
    provider_timeout_seconds: Annotated[float, Field(gt=0, le=20)]
    retry_base_seconds: Annotated[float, Field(ge=0, le=1)]
    retry_max_seconds: Annotated[float, Field(ge=0, le=2)]
    pricing: ChatPricing

    @model_validator(mode="after")
    def backoff(self) -> Self:
        if self.retry_base_seconds > self.retry_max_seconds:
            raise ValueError("retry_interval_unordered")
        return self


class PromptFile(Contract):
    name: PromptName
    sha256: Sha256
    resource_version: Literal["v1", "v2"] = "v1"


def tool_schemas_checksum() -> str:
    return canonical_sha256(
        {
            tool: {
                "request": REQUEST_MODELS[tool].model_json_schema(),
                "result": RESULT_MODELS[tool].model_json_schema(),
            }
            for tool in sorted(REQUEST_MODELS)
        }
    )


def response_schema_checksum() -> str:
    return canonical_sha256(
        {"plan": PlanDraft.model_json_schema(), "answer": AnswerDraft.model_json_schema()}
    )


class AgentChatConfig(Versioned):
    profile: Literal["agent-bounded-v1"]
    graph_version: Literal["pregraph-provider-v1", "bounded-langgraph-v1"]
    prompt_version: Symbol
    response_schema_version: Literal["agent-draft-v1"]
    model: ChatModelConfig
    budget: ModelBudget
    tool_policy: ToolPolicy
    tool_schemas_sha256: Sha256
    response_schema_sha256: Sha256
    knowledge_index_id: IndexID
    embeddings: EmbeddingConfig
    retrieval: RetrievalConfig
    prompts: Annotated[tuple[PromptFile, ...], Field(min_length=6, max_length=6)]

    @field_validator("prompts", mode="before")
    @classmethod
    def array(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def bindings(self) -> Self:
        if tuple(p.name for p in self.prompts) != PROMPT_NAMES:
            raise ValueError("prompt_bundle_incomplete_or_unordered")
        if (
            self.embeddings.provider != "bedrock"
            or self.retrieval.diversification != "score-then-document-v1"
        ):
            raise ValueError("chat_requires_semantic_knowledge_binding")
        if self.model.max_output_tokens > self.budget.max_output_tokens:
            raise ValueError("single_call_output_exceeds_run_budget")
        if (self.model.provider == "fake") != (self.budget.pricing.kind == "synthetic_test_rates"):
            raise ValueError("provider_pricing_kind_mismatch")
        return self

    def config_id(self) -> str:
        return "agent-chat-config-sha256-" + canonical_sha256(self.model_dump(mode="json"))


@dataclass(frozen=True)
class ResolvedChatConfig:
    """JSON and prompt text are a detached immutable snapshot, not mutable Pydantic lists."""

    config_json: str
    prompt_texts: tuple[tuple[PromptName, str], ...]

    @property
    def config(self) -> AgentChatConfig:
        return AgentChatConfig.model_validate_json(self.config_json)

    @property
    def config_id(self) -> str:
        return self.config.config_id()

    def verified(self) -> "ResolvedChatConfig":
        resolved = resolve_chat_config(self.config)
        if resolved.prompt_texts != self.prompt_texts:
            raise ValueError("agent_prompt_snapshot_binding_mismatch")
        return resolved


def resolve_chat_config(config: AgentChatConfig) -> ResolvedChatConfig:
    config = AgentChatConfig.model_validate_json(config.model_dump_json())
    if (
        config.tool_schemas_sha256 != tool_schemas_checksum()
        or config.response_schema_sha256 != response_schema_checksum()
    ):
        raise ValueError("agent_schema_binding_mismatch")
    resolved = []
    for prompt in config.prompts:
        # Closed package resources: no paths or templates supplied by caller/model.
        raw = (
            files("retailops_ai.agent")
            .joinpath("prompts", prompt.name + "." + prompt.resource_version + ".md")
            .read_bytes()
        )
        if not 1 <= len(raw) <= 8000 or hashlib.sha256(raw).hexdigest() != prompt.sha256:
            raise ValueError("agent_prompt_binding_mismatch")
        content = raw.decode("utf-8")
        if "\0" in content or "\r" in content:
            raise ValueError("invalid_agent_prompt_text")
        resolved.append((prompt.name, content))
    if sum(len(t.encode()) for _, t in resolved) > 16000:
        raise ValueError("agent_prompt_bundle_too_large")
    return ResolvedChatConfig(config.model_dump_json(), tuple(resolved))


def load_chat_config(path: Path) -> ResolvedChatConfig:
    try:
        with path.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("agent_config_too_large")
        strict_json(raw)
        return resolve_chat_config(AgentChatConfig.model_validate_json(raw))
    except (OSError, ValueError, RecursionError):
        raise ValueError("agent_chat_config_invalid") from None
