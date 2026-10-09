"""Real Assistant completion/SQL outbox; broker callbacks are explicit test doubles."""

import asyncio
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4, uuid5

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from test_assistant import headers, setup

from retailops_ai.api.app import create_app
from retailops_ai.assistant.contracts import AssistantAnswer, AssistantRun, PersistedSuggestion
from retailops_ai.config import Settings
from retailops_ai.intelligence_events.suggestion_contracts import (
    RecommendationGenerated,
    suggestion_event,
)
from retailops_ai.intelligence_events.suggestion_outbox import (
    deliver_suggestion_one,
    enqueue_suggestions,
)


class Message:
    def __init__(self, mode="ack"):
        self.mode = mode

    def topic(self):
        return "wrong-topic" if self.mode == "wrong_topic" else "retailops.intelligence.v2"

    def partition(self):
        return -1 if self.mode == "negative_partition" else 0

    def offset(self):
        return -1 if self.mode == "negative_offset" else 42


class Producer:
    """No socket or broker; deliberately scripted ACK/failure positions."""

    def __init__(self, mode="ack", delay=0):
        self.mode, self.delay, self.sent = mode, delay, []

    def produce(self, topic, *, key, value, on_delivery):
        self.sent.append((topic, key, value))
        self.callback = on_delivery

    def flush(self, timeout):
        assert 0 < timeout <= 15
        if self.delay:
            time.sleep(self.delay)
        if self.mode == "no_ack":
            return 0
        self.callback(
            RuntimeError("fixture failure") if self.mode == "error" else None, Message(self.mode)
        )
        if self.mode == "duplicate_ack":
            self.callback(None, Message())
        return 1 if self.mode == "pending" else 0


@pytest.fixture(scope="module")
def database(request):
    url = os.getenv("AI12_NATIVE_DATABASE_URL")
    if not url:
        if os.getenv("REQUIRE_AI12_NATIVE_POSTGRES") == "1":
            pytest.fail("dedicated AI12 SQL URL required")
        pytest.skip("dedicated AI12 SQL acceptance is not provisioned")
    engine = create_engine(url, hide_parameters=True)
    try:
        yield url, engine
    finally:
        report = os.getenv("AI12_SUGGESTION_REPORT")
        if report and request.session.testsfailed == 0:
            from retailops_ai.agent.evaluation import evaluator_checksum

            with engine.connect() as connection:
                revision = connection.scalar(text("SELECT version_num FROM ai.alembic_version"))
                counts = dict(
                    connection.execute(
                        text(
                            "SELECT status,count(*) FROM ai.assistant_suggestion_outbox GROUP BY status"
                        )
                    ).all()
                )
            Path(report).parent.mkdir(parents=True, exist_ok=True)
            Path(report).write_text(
                json.dumps(
                    dict(
                        status="passed",
                        application_code_sha256=evaluator_checksum(),
                        database_revision=revision,
                        producer="scripted_delivery_callback_no_broker",
                        aws_executed=False,
                        source_projection_executed=False,
                        policy="read-only-review-v1",
                        atomic_completion=True,
                        rollback_after_enqueue=True,
                        retry_after_ack_sql_crash=True,
                        identical_retry_bytes=True,
                        competing_workers_skip_locked=True,
                        expired_send_withheld=True,
                        stored_lifecycle_counts=counts,
                    ),
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
        engine.dispose()


@pytest.fixture(autouse=True)
def drain_test_queue(database):
    # This is the explicitly provisioned, private acceptance database only.
    while deliver_suggestion_one(database[1], Producer(), environment="test"):
        pass
    with database[1].connect() as connection:
        previous = set(
            connection.scalars(
                text("SELECT trace_id FROM ai.assistant_runs WHERE environment='test'")
            )
        )
    try:
        yield
    finally:
        # Independent cases must not spend each other's unchanged admission window.
        # Delete only this case's runs in the owned test database; outbox copies survive.
        with database[1].begin() as connection:
            current = set(
                connection.scalars(
                    text("SELECT trace_id FROM ai.assistant_runs WHERE environment='test'")
                )
            )
            for identity in current - previous:
                connection.execute(
                    text("DELETE FROM ai.assistant_runs WHERE trace_id=:id AND environment='test'"),
                    {"id": identity},
                )


def query(
    database, tmp_path, *, enabled=True, lifetime=300, expected_status=200, include_context=False
):
    from retailops_ai.agent.graph_config import load_graph_config, resolve_graph_config

    def configured(path):
        value = load_graph_config(path)
        raw = value.config.model_dump(mode="json")
        raw["policy"]["suggestions"]["lifetime_seconds"] = lifetime
        return resolve_graph_config(type(value.config).model_validate_json(json.dumps(raw)))

    with patch("test_assistant.load_graph_config", configured):
        path, tokens, authority, body, backend, _ = setup(tmp_path, intent="operations")
    raw = json.loads(path.read_bytes())
    ids = {g["principal_id"]: g["principal_id"] + "-" + uuid4().hex for g in raw["grants"]}
    for g in raw["grants"]:
        g["principal_id"] = ids[g["principal_id"]]
    for credential in raw["credentials"]:
        credential["principal_id"] = ids[credential["principal_id"]]
    from retailops_ai.security.local import LocalAccess
    from retailops_ai.security.models import AccessPolicy

    fresh = LocalAccess(AccessPolicy.model_validate_json(json.dumps(raw)))
    authority.__dict__.update(fresh.__dict__)
    path.write_text(json.dumps(raw))
    settings = Settings(
        APP_ENV="test",
        ARTIFACT_ROOT=str(tmp_path / "artifacts"),
        API_AUTH_FILE=path,
        DATABASE_URL=database[0],
        ASSISTANT_SUGGESTION_OUTBOX_ENABLED=enabled,
    )
    with TestClient(
        create_app(settings, assistant_backend=backend), base_url="http://127.0.0.1"
    ) as client:
        response = client.post("/api/v1/assistant/queries", headers=headers(tokens), json=body)
        assert response.status_code == expected_status, response.text
        return (response.json(), settings, tokens) if include_context else response.json()


def stored(engine, answer):
    with engine.connect() as connection:
        return (
            connection.execute(
                text("SELECT * FROM ai.assistant_suggestion_outbox WHERE trace_id=:trace"),
                {"trace": UUID(answer["trace_id"])},
            )
            .mappings()
            .one()
        )


def test_recommendation_reads_return_persisted_item_without_writes_or_inference(
    database, tmp_path, monkeypatch
):
    import boto3

    def forbidden(*args, **kwargs):
        pytest.fail("recommendation read constructed an AWS client")

    monkeypatch.setattr(boto3, "client", forbidden)
    monkeypatch.setattr(boto3.session.Session, "client", forbidden)
    answer, settings, tokens = query(database, tmp_path, include_context=True)
    identity = answer["recommended_actions"][0]["recommendation_id"]
    with database[1].connect() as connection:
        record = connection.scalar(
            text("SELECT record FROM ai.assistant_suggestions WHERE recommendation_id=:id"),
            {"id": UUID(identity)},
        )
        before = connection.execute(
            text("SELECT recommendation_id,status,wire_sha256 FROM ai.assistant_suggestion_outbox")
        ).all()
    from retailops_ai.adapters.assistant_store import PostgresAssistantStore

    statements = []
    original = PostgresAssistantStore._read_recommendations

    async def observed(self, *args, **kwargs):
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement.lstrip().split()[0].upper())

        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            return await original(self, *args, **kwargs)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)

    monkeypatch.setattr(PostgresAssistantStore, "_read_recommendations", observed)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        for _ in range(2):
            detail = client.get("/api/v1/recommendations/" + identity, headers=headers(tokens))
            assert detail.status_code == 200 and detail.json() == record
            page = client.get("/api/v1/recommendations", headers=headers(tokens))
            assert page.status_code == 200
            assert page.json() == {"items": [record], "next_offset": None}
        assert (
            client.get(
                "/api/v1/recommendations/" + identity, headers=headers(tokens, "foreign")
            ).status_code
            == 404
        )
        assert client.get("/api/v1/recommendations", headers=headers(tokens, "foreign")).json() == {
            "items": [],
            "next_offset": None,
        }
        assert (
            client.get(
                "/api/v1/recommendations/" + identity, headers=headers(tokens, "admin")
            ).json()
            == record
        )
        for name in ("viewer", "plain-admin"):
            assert (
                client.get("/api/v1/recommendations", headers=headers(tokens, name)).status_code
                == 403
            )
        assert client.get("/api/v1/recommendations").status_code == 401
        assert (
            client.get(
                "/api/v1/recommendations/" + str(uuid4()), headers=headers(tokens)
            ).status_code
            == 404
        )
    assert set(statements) == {"SET", "SELECT"}
    with database[1].connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT recommendation_id,status,wire_sha256 FROM ai.assistant_suggestion_outbox"
                )
            ).all()
            == before
        )


def test_recommendation_pagination_rejects_unbounded_ambiguous_and_writer_parameters(
    database, tmp_path
):
    answer, settings, tokens = query(database, tmp_path, include_context=True)
    identity = answer["recommended_actions"][0]["recommendation_id"]
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert (
            len(
                client.get("/api/v1/recommendations?limit=1", headers=headers(tokens)).json()[
                    "items"
                ]
            )
            == 1
        )
        assert client.get(
            "/api/v1/recommendations?limit=1&offset=1", headers=headers(tokens)
        ).json() == {"items": [], "next_offset": None}
        for parameters in (
            "limit=0",
            "limit=101",
            "offset=-1",
            "offset=501",
            "limit=1&limit=2",
            "offset=0&offset=1",
            "owner_id=foreign",
            "execute=true",
            "status=accepted",
        ):
            assert (
                client.get(
                    "/api/v1/recommendations?" + parameters, headers=headers(tokens)
                ).status_code
                == 422
            )
        assert (
            client.get(
                "/api/v1/recommendations/" + identity + "?execute=true", headers=headers(tokens)
            ).status_code
            == 422
        )
        for method in ("post", "put", "patch", "delete"):
            assert (
                client.request(
                    method.upper(),
                    "/api/v1/recommendations/" + identity,
                    headers=headers(tokens),
                    json={},
                ).status_code
                == 405
            )


def test_recommendation_reads_recheck_current_scope_capabilities_and_credentials(
    database, tmp_path
):
    answer, settings, tokens = query(database, tmp_path, include_context=True)
    identity = answer["recommended_actions"][0]["recommendation_id"]
    path = settings.api_auth_file
    original = json.loads(path.read_bytes())
    owner = next(g for g in original["grants"] if g["principal_id"].startswith("owner-"))
    for field in ("product_ids", "selling_location_ids", "channels", "operations:read"):
        changed = json.loads(json.dumps(original))
        grant = next(g for g in changed["grants"] if g["principal_id"] == owner["principal_id"])
        if field == "operations:read":
            grant["capabilities"].remove(field)
        else:
            grant["scope"][field] = [str(uuid4())] if field != "channels" else ["online"]
        path.write_text(json.dumps(changed))
        with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
            assert (
                client.get(
                    "/api/v1/recommendations/" + identity, headers=headers(tokens)
                ).status_code
                == 404
            )
            assert client.get("/api/v1/recommendations", headers=headers(tokens)).json() == {
                "items": [],
                "next_offset": None,
            }
    changed = json.loads(json.dumps(original))
    next(c for c in changed["credentials"] if c["principal_id"] == owner["principal_id"])[
        "revoked"
    ] = True
    path.write_text(json.dumps(changed))
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/recommendations", headers=headers(tokens)).status_code == 401


def test_expired_recommendation_is_hidden_even_from_audit_reader(database, tmp_path):
    # Leave time for real admission/completion on a CPU-constrained runner.
    # The assertion still crosses the actual persisted expiry in PostgreSQL.
    answer, settings, tokens = query(database, tmp_path, lifetime=30, include_context=True)
    identity = answer["recommended_actions"][0]["recommendation_id"]
    from datetime import UTC, datetime

    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        current = client.get("/api/v1/recommendations/" + identity, headers=headers(tokens))
        assert current.status_code == 200
        expires = PersistedSuggestion.model_validate_json(current.text).expires_at
    time.sleep(max(0, (expires - datetime.now(UTC)).total_seconds()) + 0.2)
    with TestClient(create_app(settings), base_url="http://127.0.0.1") as client:
        for name in ("owner", "admin"):
            assert (
                client.get(
                    "/api/v1/recommendations/" + identity, headers=headers(tokens, name)
                ).status_code
                == 404
            )
        assert client.get("/api/v1/recommendations", headers=headers(tokens)).json() == {
            "items": [],
            "next_offset": None,
        }


def test_api_completion_atomically_enqueues_compatible_event(database, tmp_path):
    answer = query(database, tmp_path)
    row = stored(database[1], answer)
    assert row["status"] == "pending"
    value = RecommendationGenerated.model_validate_json(json.dumps(row["document"]))
    assert value.payload.recommendation_id == UUID(
        answer["recommended_actions"][0]["recommendation_id"]
    )
    assert value.payload.trace_id == UUID(answer["trace_id"])
    assert bytes(row["wire_bytes"]) == value.wire_bytes()
    assert row["wire_sha256"] == hashlib.sha256(value.wire_bytes()).hexdigest()
    with database[1].connect() as connection:
        assert (
            connection.scalar(
                text("SELECT status FROM ai.assistant_runs WHERE trace_id=:id"),
                {"id": value.payload.trace_id},
            )
            == "succeeded"
        )


def test_default_profile_does_not_enqueue(database, tmp_path):
    answer = query(database, tmp_path, enabled=False)
    with database[1].connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM ai.assistant_suggestion_outbox WHERE trace_id=:id"),
                {"id": UUID(answer["trace_id"])},
            )
            == 0
        )


def test_expired_pending_candidate_is_withheld_without_a_broker_call(database, tmp_path):
    answer = query(database, tmp_path, lifetime=4)
    row = stored(database[1], answer)
    with database[1].connect() as connection:
        now = connection.scalar(text("SELECT clock_timestamp()"))
    time.sleep(max(0, (row["expires_at"] - now).total_seconds()) + 0.05)
    producer = Producer()
    assert not deliver_suggestion_one(database[1], producer, environment="test")
    assert producer.sent == []
    assert stored(database[1], answer)["status"] == "expired"


def test_outbox_failure_rolls_back_answer_suggestion_and_terminal_run(
    database, tmp_path, monkeypatch
):
    from retailops_ai.intelligence_events import suggestion_outbox

    original = suggestion_outbox.enqueue_suggestions

    async def fail_after_enqueue(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("scripted enqueue crash")

    monkeypatch.setattr(suggestion_outbox, "enqueue_suggestions", fail_after_enqueue)
    response = query(database, tmp_path, expected_status=503)
    with database[1].connect() as connection:
        row = (
            connection.execute(
                text("SELECT trace_id,status FROM ai.assistant_runs WHERE correlation_id=:id"),
                {"id": UUID(response["correlation_id"])},
            )
            .mappings()
            .one()
        )
        assert row["status"] == "running"
        assert (
            connection.scalar(
                text("""SELECT
              (SELECT count(*) FROM ai.assistant_answers WHERE trace_id=:id)+
              (SELECT count(*) FROM ai.assistant_suggestions WHERE trace_id=:id)+
              (SELECT count(*) FROM ai.assistant_suggestion_outbox WHERE trace_id=:id)"""),
                {"id": row["trace_id"]},
            )
            == 0
        )


@pytest.mark.parametrize(
    "mode",
    [
        "error",
        "no_ack",
        "duplicate_ack",
        "pending",
        "wrong_topic",
        "negative_partition",
        "negative_offset",
    ],
)
def test_unconfirmed_delivery_rolls_back_and_repeats_original_bytes(database, tmp_path, mode):
    answer = query(database, tmp_path)
    bad = Producer(mode)
    with pytest.raises(RuntimeError, match="delivery_(unconfirmed|position_invalid)"):
        deliver_suggestion_one(database[1], bad, environment="test")
    assert stored(database[1], answer)["status"] == "pending"
    good = Producer()
    assert deliver_suggestion_one(database[1], good, environment="test")
    assert bad.sent == good.sent
    assert stored(database[1], answer)["status"] == "delivered"
    assert not deliver_suggestion_one(database[1], good, environment="test")


def test_repeated_enqueue_is_identical_and_collision_is_rejected(database, tmp_path):
    answer = query(database, tmp_path)
    row = stored(database[1], answer)
    suggestion = PersistedSuggestion.model_validate_json(json.dumps(row["document"]["payload"]))

    async def enqueue(value):
        engine = create_async_engine(database[0], hide_parameters=True)
        try:
            async with engine.begin() as connection:
                return await enqueue_suggestions(connection, [value], environment="test")
        finally:
            await engine.dispose()

    assert asyncio.run(enqueue(suggestion)) == 1
    altered = suggestion.model_copy(
        update={"summary": "Altered envelope with the same immutable identity"}
    )
    with pytest.raises(ValueError, match="identity_collision"):
        asyncio.run(enqueue(altered))
    assert bytes(stored(database[1], answer)["wire_bytes"]) == bytes(row["wire_bytes"])


def test_database_requires_persisted_assistant_origin(database, tmp_path):
    answer = query(database, tmp_path)
    row = stored(database[1], answer)
    raw = dict(row["document"]["payload"])
    trace = uuid4()
    raw.update(
        trace_id=str(trace),
        answer_id=str(uuid5(trace, "answer")),
        recommendation_id=str(uuid5(trace, raw["candidate_id"])),
    )
    forged = suggestion_event(PersistedSuggestion.model_validate_json(json.dumps(raw)))
    wire = forged.wire_bytes()
    with database[1].begin() as connection, pytest.raises(DBAPIError, match="origin_or_capacity"):
        connection.execute(
            text("""INSERT INTO ai.assistant_suggestion_outbox
          (event_id,recommendation_id,answer_id,trace_id,environment,partition_key,
           document,wire_bytes,wire_sha256,expires_at,retain_until)
          VALUES (:id,:recommendation,:answer,:trace,'test',:key,CAST(:document AS jsonb),
                  :wire,:digest,:expires,:retain)"""),
            dict(
                id=forged.event_id,
                recommendation=forged.payload.recommendation_id,
                answer=forged.payload.answer_id,
                trace=forged.payload.trace_id,
                key=forged.partition_key,
                document=wire.decode(),
                wire=wire,
                digest=hashlib.sha256(wire).hexdigest(),
                expires=forged.payload.expires_at,
                retain=row["retain_until"],
            ),
        )


def test_expiry_during_validation_is_checked_before_produce(database, tmp_path, monkeypatch):
    from retailops_ai.intelligence_events import suggestion_outbox

    answer = query(database, tmp_path, lifetime=4)
    original = suggestion_outbox.RecommendationGenerated.model_validate_json

    def slow_validation(value):
        result = original(value)
        row = stored(database[1], answer)
        with database[1].connect() as connection:
            now = connection.scalar(text("SELECT clock_timestamp()"))
        time.sleep(max(0, (row["expires_at"] - now).total_seconds()) + 0.05)
        return result

    monkeypatch.setattr(
        suggestion_outbox.RecommendationGenerated, "model_validate_json", slow_validation
    )
    producer = Producer()
    assert not deliver_suggestion_one(database[1], producer, environment="test")
    assert producer.sent == []
    assert stored(database[1], answer)["status"] == "expired"


def test_ack_then_sql_crash_has_safe_identical_retry(database, tmp_path):
    answer = query(database, tmp_path)
    first = Producer()

    def crash(connection, cursor, statement, parameters, context, executemany):
        if "SET status='delivered'" in statement:
            raise RuntimeError("scripted SQL completion crash")

    event.listen(database[1], "before_cursor_execute", crash)
    try:
        with pytest.raises(RuntimeError, match="scripted SQL"):
            deliver_suggestion_one(database[1], first, environment="test")
    finally:
        event.remove(database[1], "before_cursor_execute", crash)
    assert stored(database[1], answer)["status"] == "pending"
    second = Producer()
    assert deliver_suggestion_one(database[1], second, environment="test")
    assert first.sent == second.sent


def test_competing_workers_publish_one_row_once(database, tmp_path):
    answer = query(database, tmp_path)
    producers = [Producer(delay=0.2), Producer(delay=0.2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda p: deliver_suggestion_one(database[1], p, environment="test"), producers
            )
        )
    assert sorted(results) == [False, True]
    assert sum(len(p.sent) for p in producers) == 1
    assert stored(database[1], answer)["status"] == "delivered"


def test_outbox_keeps_its_copy_when_assistant_records_are_pruned(database, tmp_path):
    answer = query(database, tmp_path)
    before = bytes(stored(database[1], answer)["wire_bytes"])
    # Simulate the Assistant's independent retention cleanup on this owned DB.
    with database[1].begin() as connection:
        connection.execute(
            text("DELETE FROM ai.assistant_runs WHERE trace_id=:id"),
            {"id": UUID(answer["trace_id"])},
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM ai.assistant_suggestions WHERE trace_id=:id"),
                {"id": UUID(answer["trace_id"])},
            )
            == 0
        )
    producer = Producer()
    assert deliver_suggestion_one(database[1], producer, environment="test")
    assert producer.sent[0][2] == before


def test_downgrade_cannot_discard_retained_events(database, tmp_path):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    answer = query(database, tmp_path)
    migration = importlib.import_module(
        "retailops_ai.migrations.versions.0028_ai12_suggestion_outbox"
    )
    with (
        database[1].begin() as connection,
        pytest.raises(DBAPIError, match="downgrade_requires_empty"),
    ):
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
    assert stored(database[1], answer)["status"] == "pending"


def test_outbox_rejects_mutation_premature_expiry_and_deletion(database, tmp_path):
    answer = query(database, tmp_path)
    for statement in (
        "UPDATE ai.assistant_suggestion_outbox SET wire_bytes='tampered' WHERE trace_id=:id",
        "UPDATE ai.assistant_suggestion_outbox SET status='expired',completed_at=clock_timestamp() WHERE trace_id=:id",
    ):
        with database[1].begin() as connection, pytest.raises(DBAPIError):
            connection.execute(
                text(statement),
                {"id": UUID(answer["trace_id"])},
            )
    with database[1].begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("DELETE FROM ai.assistant_suggestion_outbox WHERE trace_id=:id"),
            {"id": UUID(answer["trace_id"])},
        )
    assert stored(database[1], answer)["status"] == "pending"


def test_full_capacity_rejects_new_event_but_keeps_identical_retry(database, tmp_path):
    """Exercise the real SQL bound, without admission/load or broker claims.

    Validated copies of one HTTP result create fixture origins through the
    required running -> answer -> succeeded SQL transitions. All copied origins
    and queued events are rolled back; database guards remain enabled.
    """
    from retailops_ai.intelligence_events.suggestion_outbox import CAPACITY, MAINTAIN, PURGE

    answer = query(database, tmp_path, enabled=False)
    trace = UUID(answer["trace_id"])
    counts = text("""SELECT
      (SELECT count(*) FROM ai.assistant_runs),
      (SELECT count(*) FROM ai.assistant_answers),
      (SELECT count(*) FROM ai.assistant_suggestions),
      (SELECT count(*) FROM ai.assistant_suggestion_outbox)""")
    with database[1].connect() as connection:
        baseline = connection.execute(counts).one()
        origin = (
            connection.execute(
                text("SELECT * FROM ai.assistant_runs WHERE trace_id=:id"), {"id": trace}
            )
            .mappings()
            .one()
        )
        candidate = connection.scalar(
            text("SELECT record FROM ai.assistant_suggestions WHERE trace_id=:id"),
            {"id": trace},
        )

    async def acceptance():
        engine = create_async_engine(database[0], hide_parameters=True)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(text(MAINTAIN), {"env": "test"})
                    await connection.execute(text(PURGE), {"env": "test"})

                    async def count():
                        return await connection.scalar(
                            text(
                                "SELECT count(*) FROM ai.assistant_suggestion_outbox "
                                "WHERE environment='test'"
                            )
                        )

                    async def fixture_origin():
                        new_trace = uuid4()
                        new_answer = uuid5(new_trace, "answer")
                        recommendation = uuid5(new_trace, candidate["candidate_id"])
                        raw = dict(
                            candidate,
                            trace_id=str(new_trace),
                            answer_id=str(new_answer),
                            recommendation_id=str(recommendation),
                        )
                        suggestion = PersistedSuggestion.model_validate_json(json.dumps(raw))
                        raw = dict(answer, trace_id=str(new_trace), answer_id=str(new_answer))
                        raw["recommended_actions"] = [
                            dict(action, recommendation_id=str(recommendation))
                            for action in raw["recommended_actions"]
                        ]
                        copied_answer = AssistantAnswer.model_validate_json(json.dumps(raw))
                        completed = AssistantRun.model_validate_json(
                            json.dumps(
                                dict(
                                    origin["record"],
                                    trace_id=str(new_trace),
                                    answer_id=str(new_answer),
                                )
                            )
                        )
                        running = AssistantRun.model_validate_json(
                            json.dumps(
                                dict(
                                    completed.model_dump(mode="json"),
                                    status="running",
                                    answer_id=None,
                                    outcome=None,
                                    completed_at=None,
                                )
                            )
                        )
                        await connection.execute(
                            text("""INSERT INTO ai.assistant_runs
                          (trace_id,correlation_id,environment,owner_id,scope,access_context,
                           request_sha256,claim,requested_at,lease_until,retain_until,
                           reserved_tokens,reserved_cost,status,record)
                          SELECT :trace,:correlation,environment,owner_id,scope,access_context,
                           request_sha256,:claim,requested_at,lease_until,retain_until,
                           reserved_tokens,reserved_cost,'running',CAST(:record AS jsonb)
                          FROM ai.assistant_runs WHERE trace_id=:original"""),
                            dict(
                                trace=new_trace,
                                correlation=uuid4(),
                                claim=uuid4(),
                                original=trace,
                                record=running.model_dump_json(),
                            ),
                        )
                        await connection.execute(
                            text("""INSERT INTO ai.assistant_answers
                          (answer_id,trace_id,record) VALUES(:answer,:trace,CAST(:record AS jsonb))"""),
                            dict(
                                answer=new_answer,
                                trace=new_trace,
                                record=copied_answer.model_dump_json(),
                            ),
                        )
                        await connection.execute(
                            text("""INSERT INTO ai.assistant_suggestions
                          (recommendation_id,answer_id,trace_id,expires_at,record)
                          VALUES(:id,:answer,:trace,:expires,CAST(:record AS jsonb))"""),
                            dict(
                                id=recommendation,
                                answer=new_answer,
                                trace=new_trace,
                                expires=suggestion.expires_at,
                                record=suggestion.model_dump_json(),
                            ),
                        )
                        await connection.execute(
                            text("""UPDATE ai.assistant_runs SET
                          status='succeeded',claim=NULL,record=CAST(:record AS jsonb)
                          WHERE trace_id=:trace"""),
                            dict(
                                trace=new_trace,
                                record=completed.model_dump_json(),
                            ),
                        )
                        return suggestion

                    initial = await count()
                    assert initial < CAPACITY
                    last = None
                    for _ in range(CAPACITY - initial):
                        last = await fixture_origin()
                        assert (
                            await enqueue_suggestions(connection, [last], environment="test") == 1
                        )
                    assert last is not None
                    assert await count() == CAPACITY
                    assert await enqueue_suggestions(connection, [last], environment="test") == 1
                    assert await count() == CAPACITY
                    with pytest.raises(ValueError, match="identity_collision"):
                        async with connection.begin_nested():
                            altered = last.model_copy(update={"summary": "Full-queue collision"})
                            await enqueue_suggestions(connection, [altered], environment="test")
                    with pytest.raises(DBAPIError, match="origin_or_capacity"):
                        async with connection.begin_nested():
                            overflow = await fixture_origin()
                            await enqueue_suggestions(connection, [overflow], environment="test")
                    assert await count() == CAPACITY
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(acceptance())
    with database[1].connect() as connection:
        assert connection.execute(counts).one() == baseline
