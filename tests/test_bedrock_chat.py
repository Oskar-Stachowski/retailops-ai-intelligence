import asyncio
import copy
import json
from pathlib import Path
from threading import Event

import pytest
from botocore.exceptions import ClientError
from botocore.session import Session
from botocore.validate import validate_parameters
from test_agent_chat import PRIVATE, settings
from test_agent_tools import session

from retailops_ai.adapters.bedrock_chat import (
    BedrockChatProvider,
    CircuitBreaker,
    CircuitPolicy,
    chat_client,
    verify_eu_profile,
)
from retailops_ai.agent.bedrock_smoke import BedrockSmokeProfile, proposal, run_smoke, verify_smoke
from retailops_ai.agent.chat import ChatFailure, ChatSession, ProviderFailure
from retailops_ai.agent.chat_config import AgentChatConfig, resolve_chat_config
from retailops_ai.agent.evaluation import load_evaluation
from retailops_ai.agent.graph_config import load_graph_config
from retailops_ai.cli import main

ROOT = Path(__file__).resolve().parents[1]


def real_settings():
    value = settings().config.model_dump(mode="json")
    value["model"].update(provider="bedrock", model_id="amazon.nova-lite-v1:0", region="eu-north-1")
    value["budget"]["pricing"].update(
        kind="reviewed_provider_rates", input_per_million="0.065", output_per_million="0.26"
    )
    return resolve_chat_config(AgentChatConfig.model_validate_json(json.dumps(value)))


def policy(**changes):
    return CircuitPolicy(schema_version="1.0", **changes)


def response(body='{"kind":"tool_plan","tools":[]}', inputs=120):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": body}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": inputs, "outputTokens": 20, "totalTokens": inputs + 20},
    }


class Client:
    def __init__(self, reply=None, error=None, count=120):
        self.reply = reply or response()
        self.error = error
        self.count = count
        self.seen = []
        self.service = Session().get_service_model("bedrock-runtime")

    def count_tokens(self, **kwargs):
        validate_parameters(kwargs, self.service.operation_model("CountTokens").input_shape)
        self.seen.append(("count", kwargs))
        return {"inputTokens": self.count}

    def converse(self, **kwargs):
        validate_parameters(kwargs, self.service.operation_model("Converse").input_shape)
        self.seen.append(("converse", kwargs))
        if self.error:
            raise ClientError({"Error": {"Code": self.error, "Message": PRIVATE}}, "Converse")
        return copy.deepcopy(self.reply)


def boundary(client=None, **kwargs):
    config = real_settings()
    executor, bearer = session(settings=config.config.tool_policy)
    provider = BedrockChatProvider(config, policy(**kwargs), client=client or Client())
    run = ChatSession(executor.open_session(bearer), config, provider, jitter=lambda: 0)
    return run, provider


def test_converse_and_exact_count_share_text_input_and_sdk_valid_parameters():
    client = Client()
    run, provider = boundary(client)
    result = asyncio.run(run.call("plan", "Show sales evidence."))
    assert result.kind == "tool_plan" and result.tools == []
    count, generate = client.seen
    assert count[1]["input"]["converse"] == {
        key: generate[1][key] for key in ("system", "messages")
    }
    assert generate[1]["modelId"] == "amazon.nova-lite-v1:0"
    assert generate[1]["inferenceConfig"] == {"maxTokens": 400, "temperature": 0.0}
    assert "toolConfig" not in generate[1]
    assert run.input_tokens == 120 and run.output_tokens == 20
    assert provider.count_requests == provider.inference_requests == 1


def test_real_config_rejects_synthetic_or_zero_pricing_before_aws():
    value = real_settings().config.model_dump(mode="json")
    value["budget"]["pricing"]["input_per_million"] = "0"
    with pytest.raises(ValueError, match="positive_rates"):
        AgentChatConfig.model_validate_json(json.dumps(value))
    with pytest.raises(ValueError, match="bedrock_chat_configuration"):
        BedrockChatProvider(settings(), policy(), client=Client())


def test_generation_requires_count_and_frozen_config_binding():
    run, provider = boundary()
    request = run.preview("plan", "Show evidence.")
    with pytest.raises(ProviderFailure):
        asyncio.run(provider.generate(request))
    assert provider.inference_requests == 0
    from dataclasses import replace

    with pytest.raises(ProviderFailure):
        provider.wire(replace(request, config_id="different"))
    assert provider.count_requests == 0


def test_sdk_disables_hidden_retries_and_uses_the_credentials_chain(monkeypatch):
    import boto3

    seen = {}

    class AWS:
        def __init__(self, **kwargs):
            seen["session"] = kwargs

        def client(self, name, **kwargs):
            seen["name"] = name
            seen.update(kwargs)
            return Client()

    monkeypatch.setattr(boto3, "Session", AWS)
    chat_client(real_settings(), "approved-local-profile")
    assert seen["session"] == {"profile_name": "approved-local-profile"}
    assert seen["region_name"] == "eu-north-1"
    assert seen["config"].retries["total_max_attempts"] == 1
    assert seen["config"].read_timeout == 5


def test_unverified_cross_region_profile_fails_closed():
    value = real_settings().config.model_dump(mode="json")
    value["model"]["inference_profile"] = "eu.amazon.nova-lite-v1:0"
    config = resolve_chat_config(AgentChatConfig.model_validate_json(json.dumps(value)))
    profiles = Profiles(config)
    profiles.description["status"] = "INACTIVE"
    with pytest.raises(ValueError, match="profile_binding_invalid"):
        BedrockChatProvider(config, policy(), client=Client(), profiles=profiles)


class Profiles:
    def __init__(self, config):
        self.description = {
            "inferenceProfileId": config.config.model.inference_profile,
            "status": "ACTIVE",
            "type": "SYSTEM_DEFINED",
            "models": [
                {
                    "modelArn": "arn:aws:bedrock:eu-central-1::foundation-model/"
                    + config.config.model.model_id
                }
            ],
        }
        self.calls = []

    def get_inference_profile(self, **kwargs):
        self.calls.append(kwargs)
        return self.description


@pytest.mark.parametrize(
    "mutation", ["model", "region", "account", "duplicate", "empty", "profile", "status", "type"]
)
def test_profile_cannot_change_counted_model_or_route_outside_eu(mutation):
    value = real_settings().config.model_dump(mode="json")
    value["model"]["inference_profile"] = "eu." + value["model"]["model_id"]
    config = resolve_chat_config(AgentChatConfig.model_validate_json(json.dumps(value)))
    profiles = Profiles(config)
    if mutation in {"model", "region", "account"}:
        arn = profiles.description["models"][0]["modelArn"]
        if mutation == "model":
            arn = arn.replace(config.config.model.model_id, "unrelated-model")
        elif mutation == "region":
            arn = arn.replace("eu-central-1", "us-east-1")
        else:
            arn = arn.replace("::foundation-model", ":123456789012:foundation-model")
        profiles.description["models"][0]["modelArn"] = arn
    elif mutation == "duplicate":
        profiles.description["models"] *= 2
    elif mutation == "empty":
        profiles.description["models"] = []
    elif mutation == "profile":
        profiles.description["inferenceProfileId"] = "other-profile"
    else:
        profiles.description[mutation] = "unexpected"
    with pytest.raises(ValueError, match="profile_binding_invalid"):
        verify_eu_profile(config, profiles)


def test_verified_profile_routes_inference_but_counts_the_base_model():
    value = real_settings().config.model_dump(mode="json")
    value["model"]["inference_profile"] = "eu." + value["model"]["model_id"]
    config = resolve_chat_config(AgentChatConfig.model_validate_json(json.dumps(value)))
    client, profiles = Client(), Profiles(config)
    provider = BedrockChatProvider(config, policy(), client=client, profiles=profiles)
    executor, bearer = session(settings=config.config.tool_policy)
    run = ChatSession(executor.open_session(bearer), config, provider)
    asyncio.run(run.call("plan", "Show evidence."))
    assert client.seen[0][1]["modelId"] == config.config.model.model_id
    assert client.seen[1][1]["modelId"] == config.config.model.inference_profile
    assert provider.destination_regions == ("eu-central-1",)


@pytest.mark.parametrize("count", [True, 0, -1, "120", 1000001])
def test_invalid_count_fails_without_paid_inference(count):
    run, provider = boundary(Client(count=count))
    with pytest.raises(ChatFailure) as error:
        asyncio.run(run.call("plan", "Show evidence."))
    assert error.value.code == "provider_unavailable"
    assert provider.inference_requests == run.calls == 0


def test_input_token_limit_fails_before_paid_inference():
    run, provider = boundary(Client(count=12001))
    with pytest.raises(ChatFailure) as error:
        asyncio.run(run.call("plan", "Show evidence."))
    assert error.value.code == "budget_exceeded"
    assert provider.inference_requests == 0


@pytest.mark.parametrize(
    "change", ["tool", "reason", "usage", "bool", "cache", "role", "oversized"]
)
def test_unsafe_response_is_rejected_and_keeps_reservation(change):
    reply = response()
    if change == "tool":
        reply["output"]["message"]["content"] = [{"toolUse": {"name": "shell"}}]
    elif change == "reason":
        reply["stopReason"] = "tool_use"
    elif change == "usage":
        reply["usage"]["inputTokens"] = 121
    elif change == "bool":
        reply["usage"]["outputTokens"] = True
    elif change == "cache":
        reply["usage"]["cacheReadInputTokens"] = 1
    elif change == "role":
        reply["output"]["message"]["role"] = "user"
    else:
        reply["output"]["message"]["content"] = [{"text": "x" * 262145}]
    run, provider = boundary(Client(reply))
    with pytest.raises(ChatFailure) as error:
        asyncio.run(run.call("plan", "Show evidence."))
    assert error.value.code == "provider_unavailable"
    assert run.retries == 0 and run.input_tokens == 120 and run.output_tokens == 400
    assert provider.inference_requests == 1


def test_invalid_json_preserves_usage_and_can_use_the_single_existing_repair():
    client = Client(response("invalid JSON"))
    run, provider = boundary(client)
    with pytest.raises(ChatFailure) as error:
        asyncio.run(run.call("plan", "Show evidence."))
    assert error.value.code == "invalid_output" and run.output_tokens == 20
    client.reply = response()
    assert asyncio.run(run.call("repair", "Show evidence.")).kind == "tool_plan"
    assert run.repairs == 1 and provider.inference_requests == 2


@pytest.mark.parametrize(
    "code, retries",
    [
        ("AccessDeniedException", 0),
        ("ValidationException", 0),
        ("ThrottlingException", 2),
        ("ServiceUnavailableException", 2),
    ],
)
def test_sdk_errors_are_redacted_and_retry_stays_in_shared_budget(code, retries):
    run, provider = boundary(Client(error=code))
    with pytest.raises(ChatFailure) as error:
        asyncio.run(run.call("plan", "Show evidence."))
    assert error.value.code == "provider_unavailable" and PRIVATE not in str(error.value)
    assert run.retries == retries and provider.inference_requests == retries + 1
    assert PRIVATE not in repr(run.audit)


def test_count_success_does_not_reset_inference_outages_and_open_circuit_has_no_sdk_work():
    run, provider = boundary(Client(error="ServiceUnavailableException"), failure_threshold=2)
    with pytest.raises(ChatFailure):
        asyncio.run(run.call("plan", "Show evidence."))
    assert provider.breaker.state()["state"] == "open"
    assert provider.inference_requests == 2
    before = provider.count_requests
    with pytest.raises(ProviderFailure) as error:
        asyncio.run(provider.count_input_tokens(run.preview("plan", "Other evidence.")))
    assert error.value.code == "circuit_open" and provider.count_requests == before


def test_only_one_recovery_probe_and_old_success_cannot_close_new_circuit():
    now = [0.0]
    circuit = CircuitBreaker(policy(failure_threshold=1), lambda: now[0])
    old, failure = circuit.acquire(), circuit.acquire()
    circuit.finish(failure, "outage")
    circuit.finish(old, "ok")
    assert circuit.state()["state"] == "open"
    now[0] = 30
    count = circuit.acquire(probe_allowed=False)
    circuit.finish(count, "neutral")
    probe = circuit.acquire()
    with pytest.raises(ProviderFailure):
        circuit.acquire()
    assert circuit.state()["state"] == "half_open"
    circuit.finish(probe, "ok")
    assert circuit.state()["state"] == "closed"


def test_cancelled_sdk_work_keeps_slot_and_late_success_cannot_recover():
    started, release = Event(), Event()

    class Blocking(Client):
        def converse(self, **kwargs):
            started.set()
            assert release.wait(5)
            return super().converse(**kwargs)

    run, provider = boundary(Blocking(), failure_threshold=1, max_inflight=1)

    async def exercise():
        request = run.preview("plan", "Show evidence.")
        await provider.count_input_tokens(request)
        task = asyncio.create_task(provider.generate(request))
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.breaker.state() == {"state": "open", "inflight": 1, "failures": 1}
        with pytest.raises(ProviderFailure):
            await provider.generate(request)
        release.set()
        while provider.breaker.state()["inflight"]:
            await asyncio.sleep(0.001)
        assert provider.breaker.state()["state"] == "open"

    try:
        asyncio.run(asyncio.wait_for(exercise(), 6))
    finally:
        release.set()


def smoke_inputs(variant="bedrock"):
    offline = load_graph_config(ROOT / "agent/graph.evaluate.fake.prepaid.v4.json")
    runtime = load_graph_config(ROOT / f"agent/graph.{variant}-smoke.prepaid.v4.json")
    suite, release = load_evaluation(
        ROOT / "agent/golden.canonical.v1.json",
        ROOT / "agent/evaluation-release.fake.prepaid.v4.json",
        offline,
        ROOT / "knowledge/golden.semantic.v1.json",
        ROOT / "uv.lock",
    )
    profile = BedrockSmokeProfile.model_validate_json(
        (ROOT / f"agent/{variant}-smoke.prepaid.v4.json").read_bytes()
    )
    return profile, suite, release, offline, runtime


def test_smoke_profile_cannot_change_golden_policy_or_select_arbitrary_questions():
    profile, suite, release, offline, runtime = smoke_inputs()
    verify_smoke(profile, suite, release, offline, runtime)
    report = proposal(profile, runtime)
    assert report["aws_executed"] is False and report["real_retrieval_measured"] is False
    assert report["worst_case_estimated_usd"] == "0.15"
    altered = profile.model_copy(update={"case_ids": (*profile.case_ids[:-1], "invented")})
    with pytest.raises(ValueError, match="binding_invalid"):
        verify_smoke(altered, suite, release, offline, runtime)


def test_comparison_profile_uses_the_same_cases_oracles_and_policy():
    baseline, suite, release, offline, runtime = smoke_inputs()
    comparison = load_graph_config(ROOT / "agent/graph.sonnet-smoke.prepaid.v4.json")
    profile = BedrockSmokeProfile.model_validate_json(
        (ROOT / "agent/sonnet-smoke.prepaid.v4.json").read_bytes()
    )
    verify_smoke(profile, suite, release, offline, comparison)
    assert profile.case_ids == baseline.case_ids
    assert comparison.config.chat.model.model_id != runtime.config.chat.model.model_id
    assert comparison.config_id != runtime.config_id


@pytest.mark.parametrize("variant", ["bedrock", "document"])
def test_real_transport_smoke_over_fixtures_compares_frozen_labels_without_echoing_runtime_policy(
    variant,
):
    profile, suite, release, offline, runtime = smoke_inputs(variant)
    verify_smoke(profile, suite, release, offline, runtime)
    scripted = []
    for case_id in profile.case_ids:
        scripted.extend(next(case for case in suite.cases if case.case_id == case_id).script)

    class Replies(Client):
        def converse(self, **kwargs):
            validate_parameters(kwargs, self.service.operation_model("Converse").input_shape)
            # Independently frozen replies, not dynamic copies from sent evidence.
            step = scripted.pop(0)
            return response(step.body)

    provider = BedrockChatProvider(
        runtime.chat, profile.circuit, client=Replies(), profiles=Profiles(runtime.chat)
    )
    report = asyncio.run(run_smoke(profile, suite, runtime, provider))
    assert report["status"] == "passed" and report["cases_passed"] == 6
    assert report["data_sources"] == "frozen_fixtures" and report["ai12_closed"] is False
    assert not scripted


def test_smoke_stops_after_provider_failure_and_reports_reserved_cost():
    profile, suite, release, offline, runtime = smoke_inputs()
    provider = BedrockChatProvider(
        runtime.chat,
        profile.circuit,
        client=Client(error="AccessDeniedException"),
        profiles=Profiles(runtime.chat),
    )
    report = asyncio.run(run_smoke(profile, suite, runtime, provider))
    assert report["status"] == "failed" and len(report["cases"]) == 1
    assert report["cases"][0]["error_code"] == "provider_unavailable"
    assert report["inference_requests"] == 1
    assert report["estimated_or_reserved_usd"] == "0.008382"
    assert report["provider_diagnostics"] == [
        {
            "operation": "converse",
            "reason": "provider_rejected",
            "kind": "auth",
            "code": "AccessDeniedException",
        }
    ]
    assert PRIVATE not in json.dumps(report)


def test_invalid_response_usage_produces_bounded_numeric_diagnostic_without_content():
    run, provider = boundary(Client(reply=response(body=PRIVATE, inputs=121)))
    with pytest.raises(ChatFailure):
        asyncio.run(run.call("plan", "Show sales."))
    assert provider.diagnostics() == [
        {
            "operation": "converse",
            "reason": "response_schema_invalid",
            "counted_input_tokens": 120,
            "inputTokens": 121,
            "outputTokens": 20,
            "totalTokens": 141,
        }
    ]
    assert PRIVATE not in json.dumps(provider.diagnostics())
    provider.diagnostics()[0]["inputTokens"] = 0
    assert provider.diagnostics()[0]["inputTokens"] == 121


def test_actual_usage_below_count_tokens_settles_lower_cost_without_relaxing_upper_bound():
    run, provider = boundary(Client(count=4908, reply=response(inputs=4891)))
    result = asyncio.run(run.call("plan", "Show sales."))
    assert result.kind == "tool_plan"
    assert run.input_tokens == 4891 and run.output_tokens == 20
    assert provider.count_requests == provider.inference_requests == 1
    assert not provider.diagnostics()


@pytest.mark.parametrize("inputs", [0, -1])
def test_nonpositive_actual_input_usage_is_rejected(inputs):
    run, provider = boundary(Client(reply=response(inputs=inputs)))
    with pytest.raises(ChatFailure):
        asyncio.run(run.call("plan", "Show sales."))
    assert run.input_tokens == 120 and run.output_tokens == 400


def smoke_args():
    args = ["bedrock-smoke"]
    for flag, path in [
        ("config", "agent/graph.bedrock-smoke.prepaid.v4.json"),
        ("offline-config", "agent/graph.evaluate.fake.prepaid.v4.json"),
        ("golden", "agent/golden.canonical.v1.json"),
        ("release", "agent/evaluation-release.fake.prepaid.v4.json"),
        ("rag-golden", "knowledge/golden.semantic.v1.json"),
        ("lock", "uv.lock"),
        ("profile", "agent/bedrock-smoke.prepaid.v4.json"),
    ]:
        args.extend(["--" + flag, str(ROOT / path)])
    return args


@pytest.mark.parametrize(
    "options",
    [
        ["--execute"],
        ["--execute", "--max-cost-usd", "0.10"],
        ["--execute", "--max-cost-usd", "NaN"],
    ],
)
def test_paid_cli_requires_bound_cost_and_output_before_client_or_any_aws(
    options, capsys, tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("client must not be constructed")

    monkeypatch.setattr("retailops_ai.adapters.bedrock_chat.chat_client", forbidden)
    extra = [] if options == ["--execute"] else ["--output", str(tmp_path / "receipt.json")]
    assert main(smoke_args() + options + extra) == 2
    assert capsys.readouterr().err == '{"error":"bedrock_chat_smoke_failed"}\n'


def test_offline_cli_proposal_does_not_create_an_aws_client(capsys, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline proposal must not use AWS")

    monkeypatch.setattr("retailops_ai.adapters.bedrock_chat.chat_client", forbidden)
    output = tmp_path / "proposal.json"
    assert main(smoke_args() + ["--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["status"] == "not_run" and report["offline_gates_passed"]
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(capsys.readouterr().out)["aws_executed"] is False
