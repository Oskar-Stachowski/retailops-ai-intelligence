"""Mandatory remote PostgreSQL drill: transactions, fencing, SIGKILL and RR captures.

The transport positions and ACK callback are explicit fixtures, not a live broker.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, text
from test_observation_replay import STREAM, TIME, identifier, record, row

from retailops_ai.curated.builder import iter_rows
from retailops_ai.source_replay import (
    ObservationHistory,
    ObservationVersion,
    ReplayError,
    Stream,
)
from retailops_ai.source_replay.store import ObservationStore, TransportRecord, process_then_ack
from retailops_ai.source_replay.wire import canonical
from retailops_ai.source_snapshot.importer import verify_snapshot

ROOT = Path(__file__).resolve().parents[1]
POSTGRES = (
    "postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
)
MIGRATION = importlib.import_module("retailops_ai.migrations.versions.0021_observation_replay")


@pytest.fixture(scope="module")
def engine():
    if os.getenv("REQUIRE_AI10_OBSERVATION_TESTS") != "1":
        pytest.skip("Set REQUIRE_AI10_OBSERVATION_TESTS=1 for real disposable PostgreSQL")
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("Docker required for mandatory observation persistence acceptance")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    name = "ai10-observation-" + uuid4().hex[:10]
    db = None
    try:
        subprocess.run(
            [
                docker,
                "run",
                "-d",
                "--name",
                name,
                "--cpus",
                "1",
                "--memory",
                "512m",
                "-p",
                f"127.0.0.1:{port}:5432",
                "-e",
                "POSTGRES_USER=ai10",
                "-e",
                "POSTGRES_PASSWORD=ai10",
                "-e",
                "POSTGRES_DB=ai10",
                POSTGRES,
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        db = create_engine(
            f"postgresql+psycopg://ai10:ai10@127.0.0.1:{port}/ai10",
            hide_parameters=True,
            connect_args={"connect_timeout": 2},
        )
        deadline = time.monotonic() + 45
        while True:
            try:
                with db.connect() as connection:
                    connection.execute(text("SELECT 1"))
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        with db.begin() as connection:
            connection.exec_driver_sql(
                "CREATE SCHEMA ai; CREATE TABLE ai.previous_increment_sentinel(value text); INSERT INTO ai.previous_increment_sentinel VALUES ('preserved');"
            )
            with Operations.context(MigrationContext.configure(connection)):
                MIGRATION.upgrade()
        db.observation_test_container = name
        yield db
    finally:
        if db is not None:
            db.dispose()
        subprocess.run([docker, "rm", "-fv", name], capture_output=True, timeout=45)


def setup(engine, *, partitions=2, stream=STREAM):
    store = ObservationStore(engine)
    group = "ai10-" + uuid4().hex
    leases = [
        store.claim(group, p, stream=stream, partitions=partitions, log_low=0, log_high=10000)
        for p in range(partitions)
    ]
    return store, group, leases


def raw(fact=None, *, offset=0, partition=0, value=None, **changes):
    return TransportRecord(
        partition,
        offset,
        value
        if value is not None
        else canonical(record(fact or row(), offset, partition, **changes).envelope),
    )


def counts(engine, group):
    with engine.connect() as connection:
        state = connection.execute(
            text("SELECT fact_count,receipt_count FROM ai.observation_streams WHERE group_id=:g"),
            dict(g=group),
        ).one()
        positions = connection.scalars(
            text(
                "SELECT next_offset FROM ai.observation_partitions WHERE group_id=:g ORDER BY partition"
            ),
            dict(g=group),
        ).all()
    return state.fact_count, state.receipt_count, positions


def test_atomic_correction_dedup_ack_failure_and_restart(engine):
    store, group, leases = setup(engine)

    def failing_ack(partition, offset):
        assert counts(engine, group) == (1, 1, [1, 0])
        raise RuntimeError("transport_ack_failed")

    with pytest.raises(RuntimeError, match="transport_ack_failed"):
        process_then_ack(store, leases[0], raw(), failing_ack)
    replacement = store.claim(
        group, 0, stream=STREAM, partitions=2, log_low=0, log_high=3, broker_committed=0
    )
    assert replacement.resume_offset == 0
    acknowledgements = []
    outcome = process_then_ack(
        store, replacement, raw(), lambda *args: acknowledgements.append(args)
    )
    assert outcome.replayed and acknowledgements == [(0, 1)]
    assert store.process(replacement, raw(row(2, 4), offset=1)).outcome == "inserted"
    assert store.process(leases[1], raw(row(2, 4), partition=1)).outcome == "duplicate"
    capture = store.capture(group, stream=STREAM, partitions=2)
    restored = ObservationHistory.restore(canonical(capture), stream=STREAM, partitions=2)
    assert restored.quantity_total(TIME) == 10
    assert restored.quantity_total(TIME + timedelta(hours=1)) == 4
    assert counts(engine, group) == (2, 3, [2, 1])
    assert store.load_capture(group, capture.capture_id, stream=STREAM, partitions=2) == capture
    assert store.capture(group, stream=STREAM, partitions=2) == capture


@pytest.mark.parametrize(
    "phase,expected",
    [("before_commit", (0, 0, [0, 0])), ("after_commit_before_ack", (1, 1, [1, 0]))],
)
def test_actual_sigkill_rolls_back_or_replays_committed_transaction(engine, phase, expected):
    store, group, leases = setup(engine)
    data = asdict(leases[0])
    data["owner"], data["stream"] = str(leases[0].owner), STREAM.model_dump(mode="json")
    child = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/observation_store_child.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    try:
        child.stdin.write(
            json.dumps(
                {
                    "dsn": engine.url.render_as_string(hide_password=False),
                    "lease": data,
                    "value": base64.b64encode(raw().value).decode(),
                    "phase": phase,
                }
            )
        )
        child.stdin.close()
        assert select.select([child.stdout], [], [], 15)[0], (
            "child did not reach transaction boundary"
        )
        assert child.stdout.readline().strip() == "ready"
        child.kill()
        assert child.wait(timeout=10) == -signal.SIGKILL
        assert counts(engine, group) == expected
        lease = store.claim(
            group, 0, stream=STREAM, partitions=2, log_low=0, log_high=1, broker_committed=0
        )
        outcome = store.process(lease, raw())
        assert outcome.replayed == (phase == "after_commit_before_ack")
        assert counts(engine, group) == (1, 1, [1, 0])
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def test_receipt_insert_failure_rolls_back_fact_and_checkpoint_before_ack(engine):
    store, group, leases = setup(engine)

    def fail(conn, cursor, statement, parameters, context, many):
        if "INSERT INTO ai.observation_receipts" in statement:
            raise RuntimeError("receipt_storage_failed")

    event.listen(engine, "after_cursor_execute", fail)
    called = []
    try:
        with pytest.raises(RuntimeError, match="receipt_storage_failed"):
            process_then_ack(store, leases[0], raw(), lambda *args: called.append(args))
    finally:
        event.remove(engine, "after_cursor_execute", fail)
    assert called == [] and counts(engine, group) == (0, 0, [0, 0])
    assert store.process(leases[0], raw()).outcome == "inserted"


@pytest.mark.parametrize(
    "value",
    [
        None,
        b"",
        b"private-not-json",
        b'{"event_id":1,"event_id":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
    ],
)
def test_raw_quarantine_commits_with_checkpoint_and_blocks_capture(engine, value):
    store, group, leases = setup(engine)
    called = []
    transport = TransportRecord(
        0, 0, value, key=b"raw-key", headers=(("a", None), ("a", b"1")), timestamp_ms=5
    )
    result = process_then_ack(store, leases[0], transport, lambda *args: called.append(args))
    assert result.outcome == "quarantined" and called == [(0, 1)]
    assert counts(engine, group) == (0, 1, [1, 0])
    assert store.process(leases[0], transport).replayed
    with engine.connect() as connection:
        saved = (
            connection.execute(
                text("SELECT * FROM ai.observation_receipts WHERE group_id=:g"), dict(g=group)
            )
            .mappings()
            .one()
        )
        assert (bytes(saved.raw_value) if saved.raw_value is not None else None) == value
        assert (
            saved.transport == transport.metadata()
            and saved.reason == "observation_envelope_rejected"
        )
    with pytest.raises(ReplayError, match="quarantined_prefix"):
        store.capture(group, stream=STREAM, partitions=2)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"units": 12}, "fact_version_collision"),
        ({"version": 2, "id": identifier("row-1")}, "row_identity_collision"),
        ({"version": 2, "product_id": identifier("other")}, "history_grain_changed"),
        (
            {"observation_id": identifier("other"), "id": identifier("other-row")},
            "natural_grain_collision",
        ),
        ({"version": 2, "available_at": TIME - timedelta(seconds=1)}, "availability_regression"),
    ],
)
def test_semantic_poison_has_no_business_effect_and_durable_quarantine(engine, change, reason):
    store, group, leases = setup(engine)
    store.process(leases[0], raw())
    assert store.process(leases[0], raw(row(**change), offset=1)).outcome == "quarantined"
    assert counts(engine, group) == (1, 2, [2, 0])
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT reason FROM ai.observation_receipts WHERE group_id=:g AND offset_value=1"
                ),
                dict(g=group),
            )
            == "observation_" + reason
        )


def test_event_identity_collision_and_same_fact_different_event_are_distinct(engine):
    store, group, leases = setup(engine)
    store.process(leases[0], raw())
    event_id = record(row()).envelope.event_id
    assert (
        store.process(leases[1], raw(row(2), partition=1, event_id=event_id)).outcome
        == "quarantined"
    )
    assert store.process(leases[0], raw(offset=1)).outcome == "duplicate"
    assert counts(engine, group) == (1, 3, [2, 1])


def test_stream_partition_log_bounds_and_lease_fence_never_ack(engine):
    store, group, leases = setup(engine)
    latest = store.claim(group, 0, stream=STREAM, partitions=2, log_low=0, log_high=3)
    cases = [
        (lambda: store.process(leases[0], raw()), "lease_fenced"),
        (lambda: store.process(latest, raw(offset=2)), "offset_gap"),
        (
            lambda: store.process(latest, raw(source_authority_id=identifier("foreign"))),
            "authority_changed",
        ),
        (
            lambda: store.claim(
                group,
                0,
                stream=replace_stream(topic_id="replacement"),
                partitions=2,
                log_low=0,
                log_high=3,
            ),
            "identity_changed",
        ),
        (
            lambda: store.claim(group, 0, stream=STREAM, partitions=3, log_low=0, log_high=3),
            "vector_changed",
        ),
        (
            lambda: store.claim(group, 0, stream=STREAM, partitions=2, log_low=1, log_high=3),
            "retention_gap",
        ),
        (
            lambda: store.claim(
                group, 0, stream=STREAM, partitions=2, log_low=0, log_high=3, broker_committed=1
            ),
            "ahead_of_database",
        ),
    ]
    for operation, reason in cases:
        with pytest.raises(ReplayError, match=reason):
            operation()
        assert counts(engine, group) == (0, 0, [0, 0])
    assert store.process(latest, raw()).outcome == "inserted"
    with pytest.raises(ReplayError, match="log_rewound"):
        store.claim(group, 0, stream=STREAM, partitions=2, log_low=0, log_high=0)
    assert not store.release(leases[0]) and store.release(latest)
    with pytest.raises(ReplayError, match="lease_fenced"):
        store.process(latest, raw(offset=1))


def replace_stream(**changes):
    return Stream(**{**STREAM.model_dump(), **changes})


def test_changed_raw_bytes_or_transport_metadata_cannot_overlap(engine):
    store, group, leases = setup(engine)
    original = raw()
    store.process(leases[0], original)
    for changed in [
        replace(original, value=b" " + original.value),
        replace(original, key=b"changed"),
        replace(original, headers=(("a", b"1"),)),
        replace(original, timestamp_ms=4),
    ]:
        with pytest.raises(ReplayError, match="overlap_transport_mismatch"):
            store.process(leases[0], changed)
    assert counts(engine, group) == (1, 1, [1, 0])


def test_out_of_order_versions_require_complete_history_before_capture(engine):
    store, group, leases = setup(engine)
    store.process(leases[0], raw(row(3)))
    with pytest.raises(ReplayError, match="version_gap"):
        store.capture(group, stream=STREAM, partitions=2)
    store.process(leases[0], raw(offset=1))
    store.process(leases[0], raw(row(2), offset=2))
    assert len(store.capture(group, stream=STREAM, partitions=2).rows) == 3


def test_cross_partition_concurrent_duplicates_have_one_business_effect(engine):
    store, group, leases = setup(engine)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda p: store.process(leases[p], raw(partition=p)), [0, 1]))
    assert sorted(result.outcome for result in results) == ["duplicate", "inserted"]
    assert counts(engine, group) == (1, 2, [1, 1])
    assert len(store.capture(group, stream=STREAM, partitions=2).rows) == 1


def test_repeatable_read_capture_fails_closed_on_concurrent_boundary_change(engine):
    store, group, leases = setup(engine)
    store.process(leases[0], raw())
    changed = False

    def write_after_snapshot(conn, cursor, statement, parameters, context, many):
        nonlocal changed
        if statement.startswith("SELECT partition,next_offset") and not changed:
            changed = True
            store.process(leases[1], raw(row(2, 4), partition=1))

    event.listen(engine, "after_cursor_execute", write_after_snapshot)
    try:
        with pytest.raises(ReplayError, match="database_failure"):
            store.capture(group, stream=STREAM, partitions=2)
    finally:
        event.remove(engine, "after_cursor_execute", write_after_snapshot)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM ai.observation_captures WHERE group_id=:g"),
                dict(g=group),
            )
            == 0
        )
    capture = store.capture(group, stream=STREAM, partitions=2)
    assert [b.next_offset for b in capture.boundaries] == [1, 1] and len(capture.rows) == 2


def test_capture_load_requires_trusted_scope_and_verifies_stored_bytes(engine):
    store, group, leases = setup(engine)
    store.process(leases[0], raw())
    capture = store.capture(group, stream=STREAM, partitions=2)
    with pytest.raises(ReplayError, match="vector_changed"):
        store.load_capture(group, capture.capture_id, stream=STREAM, partitions=1)
    with pytest.raises(ReplayError, match="identity_changed"):
        store.load_capture(
            group, capture.capture_id, stream=replace_stream(cluster_id="replacement"), partitions=2
        )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE ai.observation_captures SET document=document || :suffix WHERE group_id=:g"
            ),
            dict(g=group, suffix=b" "),
        )
    with pytest.raises(ReplayError, match="stored_capture_mismatch"):
        store.load_capture(group, capture.capture_id, stream=STREAM, partitions=2)


def test_sql_capture_rejects_tampered_transport_metadata(engine):
    store, group, leases = setup(engine)
    store.process(leases[0], raw())
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE ai.observation_receipts SET transport=jsonb_set(transport,'{key}',CAST(:key AS jsonb)) WHERE group_id=:g"
            ),
            dict(g=group, key=json.dumps(base64.b64encode(b"changed").decode())),
        )
    with pytest.raises(ReplayError, match="stored_transport_mismatch"):
        store.capture(group, stream=STREAM, partitions=2)


def test_database_unavailable_does_not_ack_or_leak_connection_details(engine):
    store, group, leases = setup(engine)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused = sock.getsockname()[1]
    broken = create_engine(
        f"postgresql+psycopg://private-user:private-password@127.0.0.1:{unused}/private-db",
        hide_parameters=True,
        connect_args={"connect_timeout": 1},
    )
    called = []
    try:
        with pytest.raises(ReplayError) as caught:
            process_then_ack(
                ObservationStore(broken), leases[0], raw(), lambda *args: called.append(args)
            )
        assert str(caught.value) == "observation_database_failure" and called == []
        assert counts(engine, group) == (0, 0, [0, 0])
    finally:
        broken.dispose()


def test_database_connection_lost_mid_transaction_rolls_back_and_prevents_ack(engine):
    store, group, leases = setup(engine)
    called = []

    def terminate(conn, cursor, statement, parameters, context, many):
        if "INSERT INTO ai.observation_receipts" in statement:
            pid = conn.connection.driver_connection.info.backend_pid
            with engine.begin() as other:
                assert other.scalar(text("SELECT pg_terminate_backend(:pid)"), dict(pid=pid))

    event.listen(engine, "after_cursor_execute", terminate)
    try:
        with pytest.raises(ReplayError, match="database_failure"):
            process_then_ack(store, leases[0], raw(), lambda *args: called.append(args))
    finally:
        event.remove(engine, "after_cursor_execute", terminate)
    assert called == [] and counts(engine, group) == (0, 0, [0, 0])
    assert store.process(leases[0], raw()).outcome == "inserted"


def test_capture_count_is_bounded_and_existing_seal_remains_readable(engine):
    store, group, leases = setup(engine)
    saved = []
    for offset in range(32):
        store.process(leases[0], raw(offset=offset))
        saved.append(store.capture(group, stream=STREAM, partitions=2))
    assert store.capture(group, stream=STREAM, partitions=2) == saved[-1]
    store.process(leases[0], raw(offset=32))
    with pytest.raises(ReplayError, match="capture_count_limit"):
        store.capture(group, stream=STREAM, partitions=2)
    assert store.load_capture(group, saved[0].capture_id, stream=STREAM, partitions=2) == saved[0]


def test_groups_keep_independent_fact_and_offset_boundaries(engine):
    store, first, first_leases = setup(engine)
    _, second, second_leases = setup(engine)
    store.process(first_leases[0], raw())
    store.process(first_leases[0], raw(row(2, 4), offset=1))
    store.process(second_leases[0], raw())
    first_capture = store.capture(first, stream=STREAM, partitions=2)
    second_capture = store.capture(second, stream=STREAM, partitions=2)
    assert len(first_capture.rows) == 2 and len(second_capture.rows) == 1
    assert [b.next_offset for b in second_capture.boundaries] == [1, 0]


def test_actual_native_fixture_sql_capture_overlap_and_full_replay_equal(engine):
    snapshot_root = ROOT / "data/fixtures/ai-smoke-v1/snapshot"
    snapshot = verify_snapshot(snapshot_root)
    spec = next(
        table for table in snapshot.manifest["tables"] if table["table"] == "daily_demand_versions"
    )
    facts = sorted(
        (
            ObservationVersion.model_validate(data)
            for data in iter_rows(snapshot_root, spec["files"], 1024)
        ),
        key=lambda fact: (fact.version, fact.key),
    )
    assert len(facts) == 1612
    stream = Stream(
        source_authority_id=str(uuid5(NAMESPACE_URL, snapshot.source_id)),
        cluster_id="offline-fixture-only",
        topic_id="offline-fixture-topic+/",
    )
    positions = [0, 0, 0]
    records = []
    for fact in facts:
        p = int(fact.observation_id.replace("-", ""), 16) % 3
        records.append(
            record(
                fact,
                positions[p],
                p,
                source_authority_id=stream.source_authority_id,
                event_id=str(uuid5(NAMESPACE_URL, "fixture-event-" + fact.id)),
            )
        )
        positions[p] += 1
    reference = ObservationHistory(stream, partitions=3).apply_batch(records, stream=stream)
    store, group, leases = setup(engine, partitions=3, stream=stream)
    for item in records[:806]:
        store.process(
            leases[item.partition],
            TransportRecord(item.partition, item.offset, canonical(item.envelope)),
        )
    prefix = store.capture(group, stream=stream, partitions=3)
    assert len(prefix.rows) == 806
    assert store.load_capture(group, prefix.capture_id, stream=stream, partitions=3) == prefix
    resumed = ObservationHistory.restore(
        canonical(prefix), stream=stream, partitions=3
    ).apply_batch(records, stream=stream)
    for item in records:
        store.process(
            leases[item.partition],
            TransportRecord(item.partition, item.offset, canonical(item.envelope)),
        )
    final = store.capture(group, stream=stream, partitions=3)
    assert canonical(final) == canonical(reference.capture()) == canonical(resumed.capture())
    assert counts(engine, group) == (1612, 1612, positions)
    report = {
        "status": "passed",
        "database": "actual isolated PostgreSQL 16",
        "scope": "AI receiver projection of native daily_demand_versions; transport is fixture",
        "source_snapshot_id": snapshot.snapshot_id,
        "native_versions": len(facts),
        "captured_native_versions": len(prefix.rows),
        "prefix_capture_id": prefix.capture_id,
        "final_capture_id": final.capture_id,
        "final_capture_sha256": hashlib.sha256(canonical(final)).hexdigest(),
        "final_next_offsets": positions,
        "sql_capture_overlap_full_equal": True,
        "source_live_capture_supported": False,
        "broker_ack_performed": False,
        "full_43_table_handoff": False,
    }
    destination = os.getenv("AI10_OBSERVATION_REPORT")
    if destination:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def test_expansion_downgrade_preserves_previous_increment_data(engine):
    # Isolate destructive migration rollback from the receiver drill's database.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("CREATE DATABASE observation_migration_rollback"))
    isolated = create_engine(
        engine.url.set(database="observation_migration_rollback"), hide_parameters=True
    )
    try:
        with isolated.begin() as connection:
            connection.exec_driver_sql(
                "CREATE SCHEMA ai; CREATE TABLE ai.previous_increment_sentinel(value text); INSERT INTO ai.previous_increment_sentinel VALUES ('preserved');"
            )
            with Operations.context(MigrationContext.configure(connection)):
                MIGRATION.upgrade()
            connection.execute(
                text(
                    "INSERT INTO ai.observation_streams(group_id,stream,partitions) VALUES ('rollback','{}',1)"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                MIGRATION.downgrade()
            assert (
                connection.scalar(text("SELECT value FROM ai.previous_increment_sentinel"))
                == "preserved"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables WHERE table_schema='ai' AND table_name LIKE 'observation_%'"
                    )
                )
                == 0
            )
            with Operations.context(MigrationContext.configure(connection)):
                MIGRATION.upgrade()
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables WHERE table_schema='ai' AND table_name LIKE 'observation_%'"
                    )
                )
                == 6
            )
    finally:
        isolated.dispose()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("DROP DATABASE observation_migration_rollback"))


def test_real_pg_dump_restore_preserves_nonempty_receiver_and_sealed_capture(engine):
    store, group, leases = setup(engine)
    store.process(leases[0], raw())
    store.process(leases[0], raw(row(2, 4), offset=1))
    store.process(leases[1], TransportRecord(1, 0, b"raw-poison"))
    healthy_store, healthy_group, healthy_leases = setup(engine)
    healthy_store.process(healthy_leases[0], raw())
    capture = healthy_store.capture(healthy_group, stream=STREAM, partitions=2)
    docker = shutil.which("docker")
    container = engine.observation_test_container
    backup = subprocess.run(
        [docker, "exec", container, "pg_dump", "-U", "ai10", "--format=custom", "ai10"],
        check=True,
        capture_output=True,
        timeout=45,
    ).stdout
    assert len(backup) > 1000
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("CREATE DATABASE observation_restore"))
    restored = create_engine(engine.url.set(database="observation_restore"), hide_parameters=True)
    try:
        subprocess.run(
            [
                docker,
                "exec",
                "-i",
                container,
                "pg_restore",
                "-U",
                "ai10",
                "--exit-on-error",
                "-d",
                "observation_restore",
            ],
            input=backup,
            check=True,
            capture_output=True,
            timeout=45,
        )
        assert counts(restored, group) == counts(engine, group) == (2, 3, [2, 1])
        assert (
            ObservationStore(restored).load_capture(
                healthy_group, capture.capture_id, stream=STREAM, partitions=2
            )
            == capture
        )
        for table in [
            "observation_streams",
            "observation_partitions",
            "observation_identities",
            "observation_versions",
            "observation_receipts",
            "observation_captures",
        ]:
            query = text(f"SELECT * FROM ai.{table} ORDER BY to_jsonb({table})::text")  # noqa: S608 - fixed local table list
            with engine.connect() as original_connection, restored.connect() as restored_connection:
                original_rows = original_connection.execute(query).all()
                assert original_rows and original_rows == restored_connection.execute(query).all()
        with restored.connect() as connection:
            assert (
                connection.scalar(text("SELECT value FROM ai.previous_increment_sentinel"))
                == "preserved"
            )
    finally:
        restored.dispose()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("DROP DATABASE observation_restore"))
