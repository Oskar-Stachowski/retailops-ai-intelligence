"""Explicit real PostgreSQL/MLflow acceptance, only via the task-owned disposable runner."""

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from test_v12_inference import IMAGE, actor
from test_v12_lifecycle import artifacts as artifacts
from test_v12_lifecycle import inputs as inputs
from test_v12_lifecycle import loaded as loaded
from test_v12_lifecycle import prepared_input as prepared_input
from test_v12_lifecycle import qualification as qualification
from test_v12_lifecycle import serving as serving
from test_v12_lifecycle import tables as tables
from test_v12_lifecycle import timeline as timeline

from retailops_ai.adapters.database import EXPECTED_REVISION
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.model_lifecycle import v12_mlflow
from retailops_ai.model_lifecycle.v12_evidence import load_evidence
from retailops_ai.model_lifecycle.v12_journal import PostgresV12Journal
from retailops_ai.model_lifecycle.v12_lifecycle import V12Lifecycle
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import MODEL, TEST_MODEL
from retailops_ai.model_lifecycle.v12_registry import MLflowV12Registry, publish_approval


def test_real_services_decisions_recovery_guards_and_restart(request, tmp_path, monkeypatch):
    invocation_path = os.environ.get("AI05_V12_PRIVATE_INVOCATION")
    if not invocation_path:
        pytest.fail(
            "Run only through scripts/check_v12_lifecycle.py; private owned invocation required",
            pytrace=False,
        )
    fd = os.open(invocation_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            pytest.fail("Private invocation required", pytrace=False)
        control = json.loads(stream.read(16384))
    engine = create_engine(
        control["database_url"], hide_parameters=True, connect_args={"connect_timeout": 3}
    )
    registry = MLflowV12Registry(environment="test", port=control["mlflow_port"])
    state_file = Path(control["state_file"])
    checks = []
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT value FROM ai.service_metadata WHERE name='v12_acceptance_owner'")
                )
                == control["owner"]
            )
            assert (
                connection.scalar(text("SELECT version_num FROM ai.alembic_version"))
                == EXPECTED_REVISION
            )
        if os.environ.get("AI05_V12_RESTART_INSPECT") == "1":
            state = json.loads(state_file.read_bytes())
            journal = PostgresV12Journal(engine)
            with journal.locked(TEST_MODEL):
                active = journal.active(TEST_MODEL)
                assert active.release_id == state["release_id"]
                assert active.binding.model_version == state["model_version"]
                assert not journal.pending(TEST_MODEL)
                registry.validate(active.binding)
                assert registry.aliases(TEST_MODEL) == state["aliases"]
            state["checks"].append(
                "postgres_and_mlflow_restart_preserves_bound_release_aliases_and_completed_history"
            )
            state["status"] = "passed"
            state_file.write_bytes(canonical_bytes(state) + b"\n")
            return
        package, loaded = request.getfixturevalue("serving")
        evidence = load_evidence(loaded.export.root, loaded.export.python)
        campaign = v12_mlflow.import_evidence(
            evidence, v12_mlflow.LocalTracking(control["mlflow_port"]), tmp_path / "campaign-import"
        )
        imported = publish_approval(
            package,
            loaded,
            registry,
            campaign_mlflow_run_id=campaign["mlflow_run_id"],
            model=TEST_MODEL,
            actor=actor(),
            work=tmp_path / "approval-import",
        )
        again = publish_approval(
            package,
            loaded,
            registry,
            campaign_mlflow_run_id=campaign["mlflow_run_id"],
            model=TEST_MODEL,
            actor=actor(),
            work=tmp_path / "approval-import",
        )
        assert (
            again["status"] == "already_imported"
            and again["mlflow_run_id"] == imported["mlflow_run_id"]
        )
        checks.append(
            "real_http_approval_import_and_idempotent_repeat_preserve_original_campaign_evidence"
        )
        journal = PostgresV12Journal(engine)
        lifecycle = V12Lifecycle(registry, journal, environment="test")
        # A second process must not enter the same model decision critical section.
        with journal.locked(TEST_MODEL):
            with pytest.raises(ValueError, match="busy"):
                with PostgresV12Journal(engine).locked(TEST_MODEL):
                    pytest.fail("advisory lock must exclude another writer")
        checks.append("postgres_advisory_lock_excludes_concurrent_operator")
        # Import alias avoids colliding with pytest's request fixture below.
        from test_v12_lifecycle import request as decision

        v1 = lifecycle.execute(decision(imported, "register", "register1"), actor())[
            "model_version"
        ]
        r1 = lifecycle.execute(decision(imported, "promote", "promote1", v1), actor())
        with journal.locked(TEST_MODEL):
            assert journal.active(TEST_MODEL).release_id == r1["release_id"]
            with pytest.raises(ValueError, match="binding_conflict"):
                journal.bind("decision-another-registration", journal.binding(TEST_MODEL, v1))
        checks.append("version_cannot_be_enrolled_under_a_different_registration_decision")
        checks.append("real_version_registration_and_atomic_database_release_head")
        original_create = registry.create

        def lost(source, identity):
            original_create(source, identity)
            raise TimeoutError("explicit_test_lost_create_response")

        monkeypatch.setattr(registry, "create", lost)
        register2 = decision(imported, "register", "register2")
        with pytest.raises(TimeoutError):
            lifecycle.execute(register2, actor())
        monkeypatch.setattr(registry, "create", original_create)
        # Recreate both adapters, like a restarted operator process.
        registry = MLflowV12Registry(environment="test", port=control["mlflow_port"])
        journal = PostgresV12Journal(engine)
        lifecycle = V12Lifecycle(registry, journal, environment="test")
        v2 = lifecycle.execute(register2, actor())["model_version"]
        assert v2 != v1 and len(registry.find(TEST_MODEL, register2.decision_id)) == 1
        checks.append("real_mlflow_lost_create_response_recovers_one_version_without_post_retry")
        original_alias = registry.set_alias

        def lost_alias(model, alias, version):
            original_alias(model, alias, version)
            if alias == "rollback":
                raise TimeoutError("explicit_test_lost_alias_response")

        monkeypatch.setattr(registry, "set_alias", lost_alias)
        promote2 = decision(imported, "promote", "promote2", v2)
        with pytest.raises(TimeoutError):
            lifecycle.execute(promote2, actor())
        with journal.locked(TEST_MODEL):
            assert journal.active(TEST_MODEL).release_id == r1["release_id"]
        registry = MLflowV12Registry(environment="test", port=control["mlflow_port"])
        journal = PostgresV12Journal(engine)
        lifecycle = V12Lifecycle(registry, journal, environment="test")
        r2 = lifecycle.execute(promote2, actor())
        checks.append(
            "partial_alias_promotion_recovers_with_previous_head_preserved_until_completion"
        )
        v3 = lifecycle.execute(decision(imported, "register", "register3"), actor())[
            "model_version"
        ]
        lifecycle.execute(decision(imported, "reject", "reject3", v3), actor())
        with pytest.raises(ValueError, match="rejected_version"):
            lifecycle.execute(decision(imported, "promote", "denied3", v3), actor())
        rollback = lifecycle.execute(decision(imported, "rollback", "rollback1", v1), actor())
        assert lifecycle.execute(decision(imported, "promote", "promote1", v1), actor())["replayed"]
        with journal.locked(TEST_MODEL):
            active = journal.active(TEST_MODEL)
            assert active.release_id == rollback["release_id"]
            assert active.previous_release_id == r2["release_id"]
            assert active.restored_from_release_id == r1["release_id"]
        checks.append("reject_and_exact_previous_release_rollback_and_old_replay")
        # Failure after head/release SQL writes must roll both back if completion was not committed.
        v4 = lifecycle.execute(decision(imported, "register", "register4"), actor())[
            "model_version"
        ]
        promote4 = decision(imported, "promote", "promote4", v4)

        class LostCompletion(PostgresV12Journal):
            def append(self, identity, phase, record):
                if phase == "completed" and identity == promote4.decision_id:
                    raise RuntimeError("explicit_test_crash_before_completion")
                super().append(identity, phase, record)

        with pytest.raises(RuntimeError):
            V12Lifecycle(registry, LostCompletion(engine), environment="test").execute(
                promote4, actor()
            )
        with journal.locked(TEST_MODEL):
            assert journal.active(TEST_MODEL).release_id == rollback["release_id"]
            planned_release = journal.decision(promote4.decision_id)["release"]
            planned = planned_release["release_id"]
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ai.v12_model_releases WHERE release_id=:id"),
                    dict(id=planned),
                )
                == 0
            )
        # Even a direct SQL writer cannot commit the prepared release/head without completion.
        with pytest.raises(IntegrityError, match="v12_model_head_requires_completed_decision"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai.v12_model_releases(release_id,model_name,model_version,release,decision_id) "
                        "VALUES (:id,:model,:version,CAST(:release AS jsonb),:decision)"
                    ),
                    dict(
                        id=planned,
                        model=TEST_MODEL,
                        version=v4,
                        release=json.dumps(planned_release),
                        decision=promote4.decision_id,
                    ),
                )
                connection.execute(
                    text("UPDATE ai.v12_model_heads SET release_id=:id WHERE model_name=:model"),
                    dict(id=planned, model=TEST_MODEL),
                )
        with journal.locked(TEST_MODEL):
            assert journal.active(TEST_MODEL).release_id == rollback["release_id"]
        checks.append("deferred_database_guard_rejects_head_commit_without_completed_decision")
        final = V12Lifecycle(registry, PostgresV12Journal(engine), environment="test").execute(
            promote4, actor()
        )
        checks.append(
            "post_head_crash_rolls_back_release_head_and_completion_then_recovers_atomically"
        )
        # Database protection is exercised through real SQL, independently of application validation.
        for statement in (
            "UPDATE ai.v12_model_decisions SET record=record",
            "UPDATE ai.v12_model_steps SET record=record",
            "UPDATE ai.v12_model_versions SET binding=binding",
            "UPDATE ai.v12_model_releases SET release=release",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.exec_driver_sql(statement)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text("DELETE FROM ai.v12_model_heads"))
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE ai.v12_model_heads SET release_id=:id WHERE model_name=:model"),
                    dict(id=r1["release_id"], model=TEST_MODEL),
                )
        checks.append("real_sql_immutable_history_and_head_deletion_or_old_pointer_rejected")
        # Exact migration guard is separate from the existence of the new tables.
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE ai.alembic_version SET version_num='0014_forecast_freshness'")
            )
        try:
            with pytest.raises(ValueError, match="migration_required"):
                with PostgresV12Journal(engine).locked(TEST_MODEL):
                    pytest.fail("old revision accepted")
        finally:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE ai.alembic_version SET version_num=:revision"),
                    dict(revision=EXPECTED_REVISION),
                )
        checks.append("old_database_revision_refused")
        final_journal = PostgresV12Journal(engine)
        with final_journal.locked(TEST_MODEL):
            head = final_journal.active(TEST_MODEL)
            assert head.release_id == final["release_id"]
            assert head.binding.model_version == v4
            assert head.image_digest == IMAGE
            assert not final_journal.pending(TEST_MODEL)
            registry.validate(head.binding)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM ai.model_versions")) == 0
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ai.v12_model_versions WHERE model_name=:model"),
                    dict(model=MODEL),
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ai.v12_model_versions WHERE model_name=:model"),
                    dict(model=TEST_MODEL),
                )
                == 4
            )
        state = dict(
            version="ai05-v12-lifecycle-acceptance-1.0.0",
            status="restart_pending",
            checked_at=datetime.now(UTC).isoformat(),
            purpose="isolated_v12_lifecycle_mechanics_only",
            fixture_boundary="Original export verifier, source reconstruction, review and prediction are explicit small doubles. Real PostgreSQL, MLflow HTTP, model versions, aliases, transactions and restart are exercised.",
            checks=checks,
            model_name=TEST_MODEL,
            model_version=v4,
            release_id=head.release_id,
            aliases=registry.aliases(TEST_MODEL),
            registered_fixture_versions=4,
            migration_revision=EXPECTED_REVISION,
            original_campaign_mlflow_run_id=campaign["mlflow_run_id"],
            real_export_accepted=False,
            real_model_approved=False,
            real_production_model_registered=False,
            published_forecast_outputs=0,
            prediction_model_refits=0,
        )
        state_file.write_bytes(canonical_bytes(state) + b"\n")
    finally:
        engine.dispose()
