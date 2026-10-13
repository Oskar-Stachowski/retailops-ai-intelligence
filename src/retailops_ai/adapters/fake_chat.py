"""Exact scripted fixture provider; never interprets natural language or invokes AWS."""

import asyncio
import json
from dataclasses import dataclass
from typing import Literal

from retailops_ai.agent.chat import ChatRequest, ProviderFailure
from retailops_ai.agent.chat_config import ChatModelConfig
from retailops_ai.agent.chat_contracts import ProviderReply


@dataclass(frozen=True)
class FakeChatStep:
    request_sha256: str
    event: Literal["reply", "throttled", "transient", "auth", "schema", "timeout"]
    reply_json: str | None = None


class ScriptedChatProvider:
    source_kind: Literal["fixture"] = "fixture"

    def __init__(self, model: ChatModelConfig, steps: tuple[FakeChatStep, ...]) -> None:
        if model.provider != "fake" or not 1 <= len(steps) <= 20:
            raise ValueError("invalid_fake_chat_configuration")
        self.model = ChatModelConfig.model_validate_json(model.model_dump_json())
        self.steps = steps
        self.calls = 0

    def input_token_bound(self, request: ChatRequest) -> int:
        # Conservative bound for this exact fake serialization, not a Bedrock tokenizer.
        return len(
            json.dumps(request.payload(), ensure_ascii=False, separators=(",", ":")).encode()
        )

    async def generate(self, request: ChatRequest) -> ProviderReply:
        position = self.calls
        self.calls += 1
        if position >= len(self.steps):
            raise ProviderFailure("schema")
        step = self.steps[position]
        if step.request_sha256 != request.request_hash():
            raise ProviderFailure("schema")
        if step.event == "timeout":
            await asyncio.Event().wait()
            raise ProviderFailure("transient")
        if step.event != "reply":
            raise ProviderFailure(step.event)
        if step.reply_json is None:
            raise ProviderFailure("schema")
        return ProviderReply.model_validate_json(step.reply_json)
