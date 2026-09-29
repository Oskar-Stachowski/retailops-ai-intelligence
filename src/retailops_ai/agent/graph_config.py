"""One evaluated manifest binds graph code, evidence policy, schemas, prompts and chat budgets."""

import hashlib
from dataclasses import dataclass
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator

from retailops_ai.agent.chat_config import AgentChatConfig, ResolvedChatConfig, resolve_chat_config
from retailops_ai.agent.graph_contracts import GraphPolicy, GraphRequest, GraphResult
from retailops_ai.data_contracts.common import Sha256, Versioned
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.security.local import strict_json

BOUND_MODULES = (
    "graph_config.py",
    "chat_config.py",
    "chat_contracts.py",
    "tools.py",
    "graph.py",
    "graph_traces.py",
    "graph_contracts.py",
    "evidence.py",
    "chat.py",
    "chat_context.py",
    "execution.py",
)


def graph_code_checksum() -> str:
    package = files("retailops_ai.agent")
    return canonical_sha256(
        {
            name: hashlib.sha256(package.joinpath(name).read_bytes()).hexdigest()
            for name in BOUND_MODULES
        }
    )


def graph_schema_checksum() -> str:
    return canonical_sha256(
        {"request": GraphRequest.model_json_schema(), "result": GraphResult.model_json_schema()}
    )


class AgentGraphConfig(Versioned):
    graph_version: Literal["bounded-langgraph-v1"]
    evidence_policy_version: Literal["typed-facts-v1"]
    langgraph_version: Literal["1.2.12"]
    code_sha256: Sha256
    schemas_sha256: Sha256
    policy: GraphPolicy
    chat: AgentChatConfig

    @model_validator(mode="after")
    def chat_binding(self) -> Self:
        if self.chat.graph_version != self.graph_version or any(
            prompt.resource_version != "v2" for prompt in self.chat.prompts
        ):
            raise ValueError("graph_chat_profile_mismatch")
        return self

    def config_id(self) -> str:
        return "agent-graph-config-sha256-" + canonical_sha256(self.model_dump(mode="json"))


@dataclass(frozen=True)
class ResolvedGraphConfig:
    config_json: str

    @property
    def config(self) -> AgentGraphConfig:
        return AgentGraphConfig.model_validate_json(self.config_json)

    @property
    def config_id(self) -> str:
        return self.config.config_id()

    @property
    def chat(self) -> ResolvedChatConfig:
        return resolve_chat_config(self.config.chat)

    def verified(self) -> "ResolvedGraphConfig":
        return resolve_graph_config(self.config)


def resolve_graph_config(config: AgentGraphConfig) -> ResolvedGraphConfig:
    config = AgentGraphConfig.model_validate_json(config.model_dump_json())
    if (
        config.code_sha256 != graph_code_checksum()
        or config.schemas_sha256 != graph_schema_checksum()
        or version("langgraph") != config.langgraph_version
    ):
        raise ValueError("agent_graph_binding_mismatch")
    resolve_chat_config(config.chat)
    return ResolvedGraphConfig(config.model_dump_json())


def load_graph_config(path: Path) -> ResolvedGraphConfig:
    try:
        with path.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("graph_config_too_large")
        strict_json(raw)
        return resolve_graph_config(AgentGraphConfig.model_validate_json(raw))
    except (OSError, ValueError, RecursionError):
        raise ValueError("agent_graph_config_invalid") from None
