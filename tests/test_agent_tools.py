import asyncio
import json
import secrets
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from test_chunks import build
from test_chunks import config as config
from test_chunks import sources as sources
from test_semantic_embeddings import real_config as real_config

from retailops_ai.adapters.agent_tools import FixtureTools, PinnedKnowledgeTool
from retailops_ai.agent.execution import READ_CAPABILITIES, ToolExecutor, ToolFailure
from retailops_ai.agent.tools import INPUT, OUTPUT, DataScope, KnowledgeResult, ToolPolicy
from retailops_ai.cli import main
from retailops_ai.knowledge.contracts import REPOSITORIES
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.pipelines.indexes import build_index
from retailops_ai.pipelines.releases import validate_candidate
from retailops_ai.pipelines.retrieval import load_retrieval_config, result_from_ranked
from retailops_ai.security.local import LocalAccess, token_fingerprint
from retailops_ai.security.models import AccessPolicy, GrantTemplate

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts/agent/v1"
NOW = datetime(2026, 8, 23, tzinfo=UTC)
PRIVATE = "private-adapter-secret-marker"


def example(tool="get_sales_summary", direction="request"):
    return json.loads((CONTRACTS / f"{tool}.{direction}.v1.example.json").read_text())


def authority(capabilities=None, roles=None, scope=True):
    token = secrets.token_urlsafe(32)
    grant = {
        "principal_id": "fixture-agent-operator",
        "roles": roles or ["operator"],
        "capabilities": capabilities
        if capabilities is not None
        else ["assistant:query", *READ_CAPABILITIES.values()],
        "scope": {"product_ids": ["p-101"], "selling_location_ids": ["s-03"], "channels": ["store"]}
        if scope
        else None,
        "knowledge_scope": {
            "environment": "test",
            "repositories": list(REPOSITORIES),
            "access_classes": ["public_project"],
            "document_statuses": ["specified", "implemented", "verified"],
        }
        if capabilities is None or "knowledge:read" in capabilities
        else None,
    }
    policy = AccessPolicy.model_validate_json(
        json.dumps(
            {
                "schema_version": "1.0",
                "policy_id": "fixture-agent-policy",
                "grants": [grant],
                "credentials": [
                    {
                        "principal_id": grant["principal_id"],
                        "token_sha256": token_fingerprint(token),
                        "not_before": (NOW - timedelta(minutes=1)).isoformat(),
                        "expires_at": (NOW + timedelta(minutes=1)).isoformat(),
                        "revoked": False,
                    }
                ],
            }
        )
    )
    return LocalAccess(policy), "Bearer " + token


def policy(**updates):
    return ToolPolicy(schema_version="1.0", profile="agent-tools-bounded-v1", **updates)


class Spy:
    source_kind = "fixture"

    def __init__(self, result=None, error=None, delay=0):
        self.result = result or OUTPUT.validate_json(json.dumps(example(direction="result")))
        self.calls = 0
        self.error = error
        self.delay = delay
        self.seen = []

    async def execute(self, request, principal, pin):
        self.calls += 1
        self.seen.append((request, principal, pin))
        if self.error:
            raise self.error
        await asyncio.sleep(self.delay)
        return self.result


def session(
    adapter=None, *, tool="get_sales_summary", allow=True, settings=None, access=None, **kwargs
):
    auth, bearer = access or authority()
    adapters = {tool: adapter} if adapter else {}
    executor = ToolExecutor(
        auth,
        adapters,
        settings or policy(),
        "test",
        allow_fixtures=allow,
        clock=lambda: NOW,
        **kwargs,
    )
    return executor, bearer


def invoke(run, value=None):
    return asyncio.run(run.execute_json(json.dumps(example() if value is None else value)))


def assert_failure(run, value, code):
    with pytest.raises(ToolFailure) as failure:
        invoke(run, value)
    assert failure.value.code == code
    assert PRIVATE not in str(failure.value)
    return failure.value


def test_catalogue_has_exactly_eight_read_only_tools_and_validated_examples():
    catalogue = json.loads((CONTRACTS / "catalogue.json").read_text())
    assert {tool["tool"] for tool in catalogue["tools"]} == set(READ_CAPABILITIES)
    assert len(catalogue["tools"]) == 8 and all(t["read_only"] for t in catalogue["tools"])
    for tool in READ_CAPABILITIES:
        for direction, adapter in (("request", INPUT), ("result", OUTPUT)):
            value = example(tool, direction)
            schema = json.loads((CONTRACTS / f"{tool}.{direction}.v1.schema.json").read_text())
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(value)
            assert adapter.validate_json(json.dumps(value)).tool == tool


def test_cli_inspects_and_checks_tools_without_execution(capsys, tmp_path):
    assert main(["agent-tools"]) == 0
    assert {item["tool"] for item in json.loads(capsys.readouterr().out)["tools"]} == set(
        READ_CAPABILITIES
    )
    assert (
        main(
            [
                "agent-tool-check",
                "request",
                str(CONTRACTS / "get_sales_summary.request.v1.example.json"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "valid"
    bad = tmp_path / "invalid.json"
    bad.write_text(json.dumps({**example(), "role": PRIVATE}))
    assert main(["agent-tool-check", "request", str(bad)]) == 2
    captured = capsys.readouterr()
    assert captured.err == "agent_tool_contract_invalid\n" and PRIVATE not in captured.err


@pytest.mark.parametrize("tool", sorted(set(READ_CAPABILITIES) - {"search_knowledge"}))
def test_all_business_tools_have_exact_fixture_bindings_and_server_identity(tool):
    request = INPUT.validate_json(json.dumps(example(tool)))
    output = OUTPUT.validate_json(json.dumps(example(tool, "result")))
    fixture = FixtureTools([(request, output)])
    executor, bearer = session(fixture, tool=tool)
    run = executor.open_session(bearer)
    assert invoke(run, example(tool)) == output
    assert run.principal.principal_id == "fixture-agent-operator"
    assert run.calls == 1 and run.audit[0].status == "ok"


@pytest.mark.parametrize(
    "field", ["principal_id", "role", "tenant_id", "url", "sql", "shell", "path"]
)
@pytest.mark.parametrize("tool", sorted(READ_CAPABILITIES))
def test_identity_and_arbitrary_execution_arguments_never_reach_adapter(tool, field):
    spy = Spy()
    executor, bearer = session(spy, tool=tool)
    value = {**example(tool), field: PRIVATE}
    assert_failure(executor.open_session(bearer), value, "invalid_scope")
    assert spy.calls == 0


@pytest.mark.parametrize(
    "tool",
    [
        "execute_sql",
        "http_request",
        "shell",
        "create_order",
        "change_price",
        "promote_model",
        "knowledge_index",
    ],
)
def test_unknown_and_writing_tools_are_rejected(tool):
    spy = Spy()
    executor, bearer = session(spy)
    assert_failure(executor.open_session(bearer), {**example(), "tool": tool}, "invalid_scope")
    assert spy.calls == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"product_ids": ["p-101", "p-202"]},
        {"selling_location_ids": ["s-03", "s-04"]},
        {"channel": "online"},
    ],
)
def test_whole_scope_is_denied_before_adapter_and_audit_is_redacted(changes):
    spy = Spy()
    executor, bearer = session(spy)
    value = example()
    value["scope"].update(changes)
    run = executor.open_session(bearer)
    assert_failure(run, value, "unauthorized")
    assert spy.calls == 0 and run.calls == 0
    assert asdict(run.audit[0])["error_code"] == "unauthorized"
    assert "p-202" not in json.dumps([asdict(entry) for entry in run.audit])


@pytest.mark.parametrize(
    "capabilities,roles,scope",
    [
        (["sales:read"], ["operator"], True),
        (["sales:read"], ["viewer"], True),
        (["access:admin"], ["admin"], False),
    ],
)
def test_role_and_read_permission_do_not_implicitly_grant_assistant(capabilities, roles, scope):
    auth, bearer = authority(capabilities, roles, scope)
    executor, _ = session(Spy(), access=(auth, bearer))
    with pytest.raises(ToolFailure) as failure:
        executor.open_session(bearer)
    assert failure.value.code == "unauthorized"


def test_each_tool_requires_its_own_capability():
    spy = Spy()
    executor, bearer = session(spy, access=authority(["assistant:query"], scope=True))
    assert_failure(executor.open_session(bearer), example(), "unauthorized")
    assert spy.calls == 0


def test_missing_identity_is_denied_and_new_grants_require_explicit_role_and_scope():
    executor, _ = session(Spy())
    with pytest.raises(ToolFailure):
        executor.open_session(None)
    base = {
        "schema_version": "1.0",
        "policy_id": "example",
        "grants": [
            {
                "principal_id": "someone",
                "roles": ["admin"],
                "capabilities": ["assistant:query"],
                "scope": {
                    "product_ids": ["p-101"],
                    "selling_location_ids": ["s-03"],
                    "channels": ["store"],
                },
            }
        ],
    }
    with pytest.raises(ValidationError, match="operator"):
        GrantTemplate.model_validate_json(json.dumps(base))
    base["grants"][0].update(
        roles=["operator"], capabilities=["assistant:query", "sales:read"], scope=None
    )
    with pytest.raises(ValidationError, match="explicit_scope"):
        GrantTemplate.model_validate_json(json.dumps(base))


def test_omitted_scope_requires_a_configured_bounded_default():
    spy = Spy()
    executor, bearer = session(spy)
    value = {**example(), "scope": None}
    assert_failure(executor.open_session(bearer), value, "invalid_scope")
    scope = DataScope.model_validate_json(json.dumps(example()["scope"]))
    run = executor.open_session(bearer, default_scope=scope)
    assert invoke(run, value).status == "ok"
    assert spy.seen[0][0].scope == scope
    with pytest.raises(ToolFailure):
        executor.open_session(
            bearer, default_scope=scope.model_copy(update={"product_ids": ["p-202"]})
        )


def test_fixtures_are_opt_in_and_only_available_in_test_environment():
    spy = Spy()
    executor, bearer = session(spy, allow=False)
    assert_failure(executor.open_session(bearer), example(), "unavailable")
    assert spy.calls == 0
    auth, _ = authority()
    with pytest.raises(ValueError, match="test_environment"):
        ToolExecutor(auth, {}, policy(), "local", allow_fixtures=True)


def test_missing_upstream_or_fixture_case_is_unavailable_instead_of_zero():
    executor, bearer = session()
    assert_failure(executor.open_session(bearer), example(), "unavailable")
    request = INPUT.validate_json(json.dumps(example()))
    output = OUTPUT.validate_json(json.dumps(example(direction="result")))
    executor, bearer = session(FixtureTools([(request, output)]))
    assert_failure(executor.open_session(bearer), {**example(), "limit": 1}, "unavailable")


@pytest.mark.parametrize(
    "changes",
    [
        {"limit": 51},
        {"as_of": "2026-08-24T00:00:00Z"},
        {
            "scope": {
                "product_ids": ["p-101"] * 2,
                "selling_location_ids": ["s-03"],
                "channel": "store",
            }
        },
        {"window": {"start": "2026-01-01", "end": "2026-08-22"}},
        {"window": {"start": "2026-08-23", "end": "2026-08-22"}},
    ],
)
def test_invalid_inputs_do_not_start_a_tool(changes):
    spy = Spy()
    executor, bearer = session(spy)
    assert_failure(executor.open_session(bearer), {**example(), **changes}, "invalid_scope")
    assert spy.calls == 0


def test_stricter_server_policy_is_enforced_before_execution():
    spy = Spy()
    executor, bearer = session(spy, settings=policy(max_rows=1, max_period_days=2))
    assert_failure(executor.open_session(bearer), example(), "invalid_scope")
    assert spy.calls == 0


@pytest.mark.parametrize(
    "change",
    [
        {"product_id": "p-202"},
        {"selling_location_id": "s-04"},
        {"channel": "online"},
        {"window": {"start": "2026-08-15", "end": "2026-08-22"}},
    ],
)
def test_adapter_cannot_return_foreign_or_wrong_period_data(change):
    value = example(direction="result")
    value["items"][0].update(change)
    spy = Spy(OUTPUT.validate_json(json.dumps(value)))
    executor, bearer = session(spy)
    assert_failure(executor.open_session(bearer), example(), "unavailable")


def test_adapter_cannot_mutate_the_authorized_scope_to_validate_foreign_output():
    value = example(direction="result")
    value["items"][0]["product_id"] = "p-202"

    class Mutating(Spy):
        async def execute(self, request, principal, pin):
            request.scope.product_ids.append("p-202")
            return self.result

    executor, bearer = session(Mutating(OUTPUT.validate_json(json.dumps(value))))
    assert_failure(executor.open_session(bearer), example(), "unavailable")


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"as_of": "2026-08-23T00:00:00Z"}, "unavailable"),
        ({"as_of": "2026-08-22T23:00:00Z"}, "stale"),
        ({"freshness_status": "stale"}, "stale"),
        ({"source_kind": "runtime", "source_ref": "source-sha256-" + "a" * 64}, "unavailable"),
    ],
)
def test_freshness_and_provenance_are_checked_after_execution(changes, code):
    value = example(direction="result")
    value.update(changes)
    executor, bearer = session(Spy(OUTPUT.validate_json(json.dumps(value))))
    assert_failure(executor.open_session(bearer), example(), code)


def test_validated_dataclass_copies_do_not_bypass_output_revalidation():
    output = OUTPUT.validate_json(json.dumps(example(direction="result")))
    unsafe = output.model_copy(update={"source_ref": "https://host/" + PRIVATE})
    executor, bearer = session(Spy(unsafe))
    assert_failure(executor.open_session(bearer), example(), "unavailable")


def test_duplicate_rows_and_excess_results_are_rejected():
    value = example(direction="result")
    value["items"] *= 2
    executor, bearer = session(Spy(OUTPUT.validate_json(json.dumps(value))))
    assert_failure(executor.open_session(bearer), example(), "unavailable")
    assert_failure(executor.open_session(bearer), {**example(), "limit": 1}, "unavailable")


def test_checked_empty_source_is_distinct_from_dependency_unavailable():
    value = example(direction="result")
    value.update(status="no_data", freshness_status="missing", items=[])
    executor, bearer = session(Spy(OUTPUT.validate_json(json.dumps(value))))
    assert invoke(executor.open_session(bearer)).status == "no_data"
    value["source_ref"] = None
    with pytest.raises(ValidationError, match="checked_source"):
        OUTPUT.validate_json(json.dumps(value))


def test_exception_details_never_escape_or_enter_audit():
    executor, bearer = session(Spy(error=RuntimeError(PRIVATE)))
    run = executor.open_session(bearer)
    assert_failure(run, example(), "unavailable")
    assert PRIVATE not in json.dumps([asdict(entry) for entry in run.audit])


def test_timeout_consumes_budget_and_does_not_retry():
    spy = Spy(delay=0.2)
    executor, bearer = session(spy, settings=policy(max_calls=1, tool_timeout_seconds=0.01))
    run = executor.open_session(bearer)
    assert_failure(run, example(), "budget_exceeded")
    assert_failure(run, example(), "budget_exceeded")
    assert spy.calls == run.calls == 1


def test_concurrent_calls_share_one_budget():
    async def scenario():
        spy = Spy(delay=0.01)
        executor, bearer = session(spy)
        run = executor.open_session(bearer)
        results = await asyncio.gather(
            *(run.execute_json(json.dumps(example())) for _ in range(10)), return_exceptions=True
        )
        assert sum(isinstance(result, ToolFailure) for result in results) == 4
        assert spy.calls == run.calls == 6

    asyncio.run(scenario())


def test_request_deadline_is_not_reset_between_tools_or_after_completion():
    clock = [0.0]
    spy = Spy()
    executor, bearer = session(spy, timer=lambda: clock[0])
    run = executor.open_session(bearer)
    clock[0] = 45.0
    assert_failure(run, example(), "budget_exceeded")
    assert spy.calls == 0
    clock[0] = 0.0

    class Late(Spy):
        async def execute(self, request, principal, pin):
            result = await super().execute(request, principal, pin)
            clock[0] = 46.0
            return result

    executor, bearer = session(Late(), timer=lambda: clock[0])
    assert_failure(executor.open_session(bearer), example(), "budget_exceeded")


def test_cancellation_consumes_budget_and_preserves_a_safe_trace():
    async def scenario():
        spy = Spy(delay=1)
        executor, bearer = session(spy, settings=policy(max_calls=1))
        run = executor.open_session(bearer)
        task = asyncio.create_task(run.execute_json(json.dumps(example())))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert run.calls == 1 and run.audit[-1].error_code == "cancelled"
        with pytest.raises(ToolFailure):
            await run.execute_json(json.dumps(example()))

    asyncio.run(scenario())


@pytest.fixture
def semantic_pin(sources, config, real_config):
    class FixtureEmbedding:
        def __init__(self, cfg):
            self.config = cfg

        def embed(self, text):
            return (1.0,) + (0.0,) * (self.config.dimension - 1)

    candidate = build_index(
        build(sources, config), real_config, provider=FixtureEmbedding(real_config)
    )
    pin = IndexPin(
        schema_version="1.0",
        environment="test",
        lane="retrieval",
        purpose="qualified_semantic_retrieval",
        generation=1,
        request_id="rag-change-" + "a" * 32,
        review_id="corpus-review-sha256-" + "b" * 64,
        validation_id=validate_candidate(candidate).validation_id,
        manifest=candidate.manifest,
    )
    return pin, candidate


class KnowledgeSpy:
    def __init__(self, candidate):
        self.candidate = candidate
        self.pins = []

    def search_pinned(self, pin, request, principal):
        self.pins.append((pin, principal))
        config = load_retrieval_config(ROOT / "knowledge/retrieval.semantic.v1.json")
        return result_from_ranked(
            pin.manifest, request, config, [(chunk, 1.0) for chunk in self.candidate.chunks.chunks]
        )


def test_knowledge_tool_uses_the_server_pin_and_verified_principal(semantic_pin):
    pin, candidate = semantic_pin
    backend = KnowledgeSpy(candidate)
    executor, bearer = session(PinnedKnowledgeTool(backend), tool="search_knowledge", allow=False)
    run = executor.open_session(bearer, pin=pin)
    value = example("search_knowledge")
    value["retrieval"]["purpose"] = "documentation"
    result = invoke(run, value)
    assert isinstance(result, KnowledgeResult)
    assert result.status == "ok" and result.items
    assert result.provider == "bedrock" and result.content_trust == "untrusted_reference"
    assert result.index_id == pin.manifest.index_id
    assert backend.pins == [(pin, run.principal)]
    assert result.items[0].chunk.occurrences[0].source_ref


def test_knowledge_scope_and_missing_pin_are_checked_before_backend(semantic_pin):
    pin, candidate = semantic_pin
    backend = KnowledgeSpy(candidate)
    executor, bearer = session(PinnedKnowledgeTool(backend), tool="search_knowledge", allow=False)
    value = example("search_knowledge")
    assert_failure(executor.open_session(bearer), value, "unavailable")
    value["retrieval"]["filters"] = {"access_classes": ["restricted"]}
    assert_failure(executor.open_session(bearer, pin=pin), value, "unauthorized")
    assert backend.pins == []


def test_adapter_cannot_switch_index_behind_the_agent_pin(semantic_pin):
    pin, candidate = semantic_pin

    class WrongIndex(KnowledgeSpy):
        def search_pinned(self, pin, request, principal):
            return (
                super()
                .search_pinned(pin, request, principal)
                .model_copy(update={"index_id": "index-sha256-" + "f" * 64})
            )

    executor, bearer = session(
        PinnedKnowledgeTool(WrongIndex(candidate)), tool="search_knowledge", allow=False
    )
    assert_failure(
        executor.open_session(bearer, pin=pin), example("search_knowledge"), "unavailable"
    )
