"""Text-only Converse boundary: exact token preflight, bounded SDK work and circuit breaker."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Annotated, Literal, Protocol, cast

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from pydantic import Field

from retailops_ai.agent.chat import ChatRequest, ProviderFailure
from retailops_ai.agent.chat_config import ResolvedChatConfig
from retailops_ai.agent.chat_contracts import AnswerDraft, PlanDraft, ProviderReply, ProviderUsage
from retailops_ai.data_contracts.common import Versioned

TRANSPORT_VERSION = "converse-json-counted-v1"


class CircuitPolicy(Versioned):
    failure_threshold: Annotated[int, Field(ge=1, le=10)] = 3
    recovery_seconds: Annotated[float, Field(gt=0, le=300)] = 30.0
    max_inflight: Annotated[int, Field(ge=1, le=8)] = 2


@dataclass
class _Ticket:
    epoch: int
    probe: bool
    cancelled: bool = False


class CircuitBreaker:
    """Late SDK success cannot undo a timeout or close a newer open circuit."""

    def __init__(self, policy: CircuitPolicy, timer: Callable[[], float] = monotonic) -> None:
        self.policy = CircuitPolicy.model_validate_json(policy.model_dump_json())
        self.timer = timer
        self._lock = Lock()
        self._epoch = 0
        self._failures = 0
        self._open_until: float | None = None
        self._probe = False
        self._inflight = 0

    def acquire(self, *, probe_allowed: bool = True) -> _Ticket:
        with self._lock:
            if self._inflight >= self.policy.max_inflight:
                raise ProviderFailure("circuit_open")
            recovering = self._open_until is not None
            if recovering and (self.timer() < cast(float, self._open_until) or self._probe):
                raise ProviderFailure("circuit_open")
            probe = recovering and probe_allowed
            if probe:
                self._probe = True
            self._inflight += 1
            return _Ticket(self._epoch, probe)

    def _fail(self, ticket: _Ticket) -> None:
        if ticket.epoch != self._epoch:
            return
        self._failures += 1
        if ticket.probe or self._failures >= self.policy.failure_threshold:
            self._epoch += 1
            self._open_until = self.timer() + self.policy.recovery_seconds
            self._probe = False

    def cancel(self, ticket: _Ticket) -> None:
        with self._lock:
            if not ticket.cancelled:
                ticket.cancelled = True
                self._fail(ticket)

    def finish(
        self, ticket: _Ticket, result: Literal["ok", "outage", "rejected", "neutral"]
    ) -> None:
        with self._lock:
            self._inflight -= 1
            if ticket.cancelled or ticket.epoch != self._epoch:
                return
            if result == "outage":
                self._fail(ticket)
            elif result == "ok":
                self._failures = 0
                if ticket.probe:
                    self._epoch += 1
                    self._open_until = None
                    self._probe = False
            elif result == "rejected" and ticket.probe:
                # An auth/schema rejection is not a recovery proof.
                self._fail(ticket)

    def state(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": "half_open" if self._probe else "open" if self._open_until else "closed",
                "inflight": self._inflight,
                "failures": self._failures,
            }


class ConverseClient(Protocol):
    def converse(self, **kwargs: object) -> dict[str, object]: ...
    def count_tokens(self, **kwargs: object) -> dict[str, object]: ...


def chat_client(config: ResolvedChatConfig, profile: str | None = None) -> ConverseClient:
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config  # type: ignore[import-untyped]

    timeout = config.config.budget.provider_timeout_seconds
    return cast(
        ConverseClient,
        boto3.Session(profile_name=profile).client(
            "bedrock-runtime",
            region_name=config.config.model.region,
            config=Config(
                connect_timeout=min(2, timeout),
                read_timeout=timeout,
                retries={"total_max_attempts": 1, "mode": "standard"},
                max_pool_connections=2,
            ),
        ),
    )


def _failure(error: ClientError) -> ProviderFailure:
    code = error.response.get("Error", {}).get("Code", "")
    if code == "ThrottlingException":
        return ProviderFailure("throttled")
    if code in {
        "InternalServerException",
        "ServiceUnavailableException",
        "ModelNotReadyException",
        "ModelTimeoutException",
    }:
        return ProviderFailure("transient")
    original = error.response.get("originalStatusCode")
    if code == "ModelErrorException" and type(original) is int and original >= 500:
        return ProviderFailure("transient")
    if code in {
        "AccessDeniedException",
        "UnrecognizedClientException",
        "ExpiredTokenException",
        "InvalidSignatureException",
    }:
        return ProviderFailure("auth")
    return ProviderFailure("schema")


class BedrockChatProvider:
    source_kind: Literal["runtime"] = "runtime"

    def __init__(
        self,
        config: ResolvedChatConfig,
        policy: CircuitPolicy,
        *,
        client: ConverseClient | None = None,
        profile: str | None = None,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self.config = config.verified()
        self.model = self.config.config.model
        if self.model.provider != "bedrock":
            raise ValueError("bedrock_chat_configuration_required")
        if self.model.inference_profile is not None:
            # A profile needs independently verified destination-model/rate bindings.
            raise ValueError("in_region_bedrock_chat_required")
        self.client = client or chat_client(self.config, profile)
        self.breaker = CircuitBreaker(policy, timer)
        self._counts: dict[str, int] = {}
        self._lock = Lock()
        self.count_requests = 0
        self.inference_requests = 0

    def wire(self, request: ChatRequest) -> dict[str, object]:
        if (
            request.config_id != self.config.config_id
            or request.system_prompts != tuple(text for _, text in self.config.prompt_texts)
            or request.max_output_tokens != self.model.max_output_tokens
        ):
            raise ProviderFailure("schema")
        payload = request.payload()
        schema = PlanDraft if request.expected_kind == "tool_plan" else AnswerDraft
        system = (
            "\n\n".join(request.system_prompts)
            + "\nReturn only JSON matching this schema:\n"
            + json.dumps(
                schema.model_json_schema(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        user = json.dumps(
            {
                key: value
                for key, value in payload.items()
                if key not in {"system", "max_output_tokens"}
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(system.encode()) + len(user.encode()) > 131072:
            raise ProviderFailure("schema")
        return {
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": user}]}],
        }

    def input_token_bound(self, request: ChatRequest) -> int:
        # No byte/token guess can authorize inference. The counted interface is required.
        with self._lock:
            count = self._counts.get(request.request_hash())
        if count is None:
            raise ProviderFailure("schema")
        return count

    async def _invoke(
        self, operation: Literal["count_tokens", "converse"], payload: dict[str, object]
    ) -> dict[str, object]:
        ticket = self.breaker.acquire(probe_allowed=operation == "converse")

        def work() -> dict[str, object]:
            result: Literal["ok", "outage", "rejected", "neutral"] = "rejected"
            try:
                with self._lock:
                    if operation == "count_tokens":
                        self.count_requests += 1
                    else:
                        self.inference_requests += 1
                response = getattr(self.client, operation)(**payload)
                result = "ok" if operation == "converse" else "neutral"
                return cast(dict[str, object], response)
            except ClientError as exc:
                failure = _failure(exc)
                result = "outage" if failure.code in {"throttled", "transient"} else "rejected"
                raise failure from None
            except BotoCoreError:
                result = "outage"
                raise ProviderFailure("transient") from None
            except Exception:
                result = "outage"
                raise ProviderFailure("transient") from None
            finally:
                self.breaker.finish(ticket, result)

        worker = asyncio.create_task(asyncio.to_thread(work))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Cancellation cannot stop a synchronous SDK call; hold its slot until it exits.
            self.breaker.cancel(ticket)
            worker.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            raise

    async def count_input_tokens(self, request: ChatRequest) -> int:
        response = await self._invoke(
            "count_tokens",
            {"modelId": self.model.model_id, "input": {"converse": self.wire(request)}},
        )
        count = response.get("inputTokens")
        if type(count) is not int or not 0 < count <= 1000000:
            raise ProviderFailure("schema")
        with self._lock:
            if len(self._counts) >= 64:
                self._counts.pop(next(iter(self._counts)))
            self._counts[request.request_hash()] = count
        return count

    async def generate(self, request: ChatRequest) -> ProviderReply:
        counted = self.input_token_bound(request)
        response = await self._invoke(
            "converse",
            self.wire(request)
            | {
                "modelId": self.model.inference_profile or self.model.model_id,
                "inferenceConfig": {
                    "maxTokens": request.max_output_tokens,
                    "temperature": self.model.temperature,
                },
            },
        )
        try:
            raw = json.dumps(response, ensure_ascii=False).encode()
            if len(raw) > 262144:
                raise ValueError("oversized_provider_response")
            message = cast(
                dict[str, object], cast(dict[str, object], response["output"])["message"]
            )
            content = message["content"]
            usage = cast(dict[str, object], response["usage"])
            inputs, outputs, total = (
                usage["inputTokens"],
                usage["outputTokens"],
                usage["totalTokens"],
            )
            if type(inputs) is not int or type(outputs) is not int or type(total) is not int:
                raise ValueError("invalid_provider_usage")
            if (
                message.get("role") != "assistant"
                or response.get("stopReason") not in {"end_turn", "max_tokens"}
                or not isinstance(content, list)
                or not 1 <= len(content) <= 16
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"text"}
                    or not isinstance(item["text"], str)
                    for item in content
                )
                or inputs != counted
                or outputs < 0
                or outputs > request.max_output_tokens
                or total != inputs + outputs
                or usage.get("cacheReadInputTokens", 0) != 0
                or usage.get("cacheWriteInputTokens", 0) != 0
            ):
                raise ValueError("invalid_provider_response")
            body = "".join(item["text"] for item in content)
            # Truncation and invalid JSON retain trustworthy usage and flow to one repair.
            return ProviderReply(
                body=body,
                usage=ProviderUsage(input_tokens=inputs, output_tokens=outputs),
            )
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            raise ProviderFailure("schema") from None
