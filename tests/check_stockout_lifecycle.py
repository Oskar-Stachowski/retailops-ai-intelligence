"""Owned real PostgreSQL/MLflow stockout lifecycle and backup; synthetic mechanics only."""

import json
import os
import stat
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from test_model_lifecycle import actor
from test_stockout_conditional_runtime import conditional as conditional
from test_stockout_lifecycle import request as decision
from test_stockout_release import backend as backend
from test_stockout_release import capsule as capsule
from test_stockout_release import context as context
from test_stockout_release import job as job
from test_stockout_release import records as records
from test_stockout_release import source as source

from retailops_ai.adapters.database import EXPECTED_REVISION
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.stockout_lifecycle.contract import MODEL, TEST_MODEL
from retailops_ai.stockout_lifecycle.engine import StockoutLifecycle
from retailops_ai.stockout_lifecycle.journal import PostgresStockoutJournal
from retailops_ai.stockout_lifecycle.publish import publish_approval
from retailops_ai.stockout_lifecycle.registry import MLflowStockoutRegistry


class LostResponseRegistry(MLflowStockoutRegistry):
    """Faults occur after real MLflow writes; retries must discover the retained version."""

    fail_create_after = False
    fail_alias = None

    def create(self, source, decision):
        result = super().create(source, decision)
        if self.fail_create_after:
            self.fail_create_after = False
            raise TimeoutError("explicit_stockout_lost_real_registration_response")
        return result

    def set_alias(self, model, alias, version):
        super().set_alias(model, alias, version)
        if self.fail_alias == alias:
            self.fail_alias = None
            raise TimeoutError("explicit_stockout_lost_real_alias_response")


def owned_control():
    path = os.environ.get("AI05_V12_PRIVATE_INVOCATION")
    if not path:
        pytest.fail("Owned disposable AI05 runner invocation required", pytrace=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            pytest.fail("Private owned invocation required", pytrace=False)
        return json.loads(stream.read(16384))


def test_real_stockout_postgres_recovery_atomicity_guards_and_restart(request):
    control = owned_control()
    engine = create_engine(
        control["database_url"], hide_parameters=True, connect_args={"connect_timeout": 3}
    )
    state_file = Path(control["state_file"] + ".stockout.json")
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
        journal = PostgresStockoutJournal(engine)
        registry = LostResponseRegistry(environment="test", port=control["mlflow_port"])
        if os.environ.get("AI05_V12_RESTART_INSPECT") == "1":
            state = json.loads(state_file.read_bytes())
            with journal.locked(TEST_MODEL):
                active = journal.active(TEST_MODEL)
                assert active.release_id == state["release_id"]
                assert active.binding.model_version == state["model_version"]
                assert active.runtime_pin().model_name == TEST_MODEL
                assert not journal.pending(TEST_MODEL)
            registry.validate(active.binding, current=False)
            aliases = registry.aliases(TEST_MODEL)
            assert aliases.get("champion") == active.binding.model_version
            assert aliases.get("rollback") == active.previous_version
            state["checks"].append(
                "real_postgres_restart_preserves_complete_stockout_binding_and_head"
            )
            state["status"] = "passed"
            state_file.write_bytes(canonical_bytes(state) + b"\n")
            return
        root, approval = request.getfixturevalue("capsule")
        imported = publish_approval(
            root,
            registry,
            approval_id=approval.release_id,
            model=TEST_MODEL,
            actor=actor(),
            work=state_file.parent / "stockout-approval-work",
        )
        assert not imported["registered"] and not imported["promoted"]
        assert (
            publish_approval(
                root,
                registry,
                approval_id=approval.release_id,
                model=TEST_MODEL,
                actor=actor(),
                work=state_file.parent / "stockout-approval-work",
            )["status"]
            == "already_imported"
        )
        source = registry.source(imported["mlflow_run_id"], imported["approval_sha256"], TEST_MODEL)
        registry.original = source
        checks.append(
            "real_mlflow_capsule_byte_smoke_verification_and_idempotent_import_before_registration"
        )
        lifecycle = StockoutLifecycle(registry, journal, environment="test")
        with journal.locked(TEST_MODEL):
            with pytest.raises(ValueError, match="busy"):
                with PostgresStockoutJournal(engine).locked(TEST_MODEL):
                    pytest.fail("same namespace lock must exclude another writer")
        checks.append("shared_ai05_advisory_lock_serializes_stockout_namespace")
        with engine.connect() as connection:

            def capsule_valid(value):
                return connection.scalar(
                    text("SELECT ai.stockout_approved_capsule_valid(CAST(:value AS jsonb))"),
                    dict(value=json.dumps(value)),
                )

            approved = source.approval.model_dump(mode="json")
            assert capsule_valid(approved) is True
            for change in ("quality", "gate", "missing_gate", "recipe_pin", "boolean"):
                c = deepcopy(approved)
                if change == "quality":
                    c["qualification"]["purpose"] = "qualified_stockout"
                elif change == "gate":
                    c["approval"]["gates"]["calibration"]["status"] = "failed"
                elif change == "missing_gate":
                    del c["approval"]["gates"]["threshold_capacity"]
                elif change == "recipe_pin":
                    c["qualification"]["recipe"]["pin"]["model_id"] = (
                        "risk-model-sha256-" + "0" * 64
                    )
                else:
                    c["qualification"]["complete_pipeline_verified"] = "true"
                assert capsule_valid(c) is False
        checks.append(
            "real_sql_requires_twelve_gates_matching_pins_strict_booleans_and_final_quality"
        )
        v1 = lifecycle.execute(decision(registry, "register", "db-register-one"), actor())[
            "model_version"
        ]
        promote1 = decision(registry, "promote", "db-promote-one", v1)
        r1 = lifecycle.execute(promote1, actor())
        with journal.locked(TEST_MODEL):
            first = journal.active(TEST_MODEL)
            assert first.release_id == r1["release_id"]
            with pytest.raises(ValueError, match="binding_conflict"):
                journal.bind("decision-stockout-other-registration", first.binding)
        registry.fail_create_after = True
        register2 = decision(registry, "register", "db-register-two")
        with pytest.raises(TimeoutError):
            lifecycle.execute(register2, actor())
        lifecycle = StockoutLifecycle(registry, PostgresStockoutJournal(engine), environment="test")
        v2 = lifecycle.execute(register2, actor())["model_version"]
        assert len(registry.find(TEST_MODEL, register2.decision_id)) == 1
        registry.fail_alias = "rollback"
        promote2 = decision(registry, "promote", "db-promote-two", v2)
        with pytest.raises(TimeoutError):
            lifecycle.execute(promote2, actor())
        with journal.locked(TEST_MODEL):
            assert journal.active(TEST_MODEL) == first
        r2 = lifecycle.execute(promote2, actor())
        rollback = lifecycle.execute(decision(registry, "rollback", "db-rollback-one", v1), actor())
        assert lifecycle.execute(promote1, actor())["replayed"]
        with journal.locked(TEST_MODEL):
            active = journal.active(TEST_MODEL)
            assert active.release_id == rollback["release_id"]
            assert active.previous_release_id == r2["release_id"]
            assert active.restored_from_release_id == first.release_id
        checks.append(
            "postgres_recovers_lost_registry_response_and_partial_aliases_then_exact_rollback_without_old_replay_reverting_head"
        )
        v3 = lifecycle.execute(decision(registry, "register", "db-register-three"), actor())[
            "model_version"
        ]
        promote3 = decision(registry, "promote", "db-promote-three", v3)

        class LostCompletion(PostgresStockoutJournal):
            def append(self, identity, phase, record):
                if phase == "completed" and identity == promote3.decision_id:
                    raise RuntimeError("explicit_stockout_test_crash_before_completion")
                super().append(identity, phase, record)

        with pytest.raises(RuntimeError, match="crash_before_completion"):
            StockoutLifecycle(registry, LostCompletion(engine), environment="test").execute(
                promote3, actor()
            )
        with journal.locked(TEST_MODEL):
            assert journal.active(TEST_MODEL).release_id == rollback["release_id"]
            planned = journal.decision(promote3.decision_id)["release"]
        with pytest.raises(IntegrityError, match="stockout_model_head_requires_completed_decision"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO ai.stockout_model_releases(release_id,model_name,model_version,release,decision_id) VALUES (:id,:model,:version,CAST(:release AS jsonb),:decision)"
                    ),
                    dict(
                        id=planned["release_id"],
                        model=TEST_MODEL,
                        version=v3,
                        release=json.dumps(planned),
                        decision=promote3.decision_id,
                    ),
                )
                connection.execute(
                    text(
                        "UPDATE ai.stockout_model_heads SET release_id=:id WHERE model_name=:model"
                    ),
                    dict(id=planned["release_id"], model=TEST_MODEL),
                )
        final = lifecycle.execute(promote3, actor())
        checks.append(
            "head_release_and_completion_are_atomic_and_deferred_sql_guard_refuses_incomplete_direct_writer"
        )
        for statement in (
            "UPDATE ai.stockout_model_decisions SET record=record",
            "UPDATE ai.stockout_model_steps SET record=record",
            "UPDATE ai.stockout_model_versions SET binding=binding",
            "UPDATE ai.stockout_model_releases SET release=release",
            "DELETE FROM ai.stockout_model_heads",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.exec_driver_sql(statement)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE ai.stockout_model_heads SET release_id=:id WHERE model_name=:model"
                    ),
                    dict(id=r1["release_id"], model=TEST_MODEL),
                )
        checks.append(
            "real_sql_history_is_append_only_and_completed_head_cannot_be_repointed_to_old_release"
        )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                forged = deepcopy(source.model_dump(mode="json"))
                forged["model_name"], forged["model_version"] = MODEL, v1
                request_body = decision(registry, "register", "db-forged-namespace").model_dump(
                    mode="json"
                )
                request_body["model_name"] = MODEL
                record = dict(request=request_body, request_sha256="0" * 64)
                connection.execute(
                    text(
                        "INSERT INTO ai.stockout_model_decisions(decision_id,model_name,request_sha256,record) VALUES (:id,:model,:sha,CAST(:record AS jsonb))"
                    ),
                    dict(
                        id=request_body["decision_id"],
                        model=MODEL,
                        sha="0" * 64,
                        record=json.dumps(record),
                    ),
                )
                connection.execute(
                    text(
                        "INSERT INTO ai.stockout_model_versions(model_name,model_version,binding,decision_id) VALUES (:model,:version,CAST(:binding AS jsonb),:decision)"
                    ),
                    dict(
                        model=MODEL,
                        version=v1,
                        binding=json.dumps(forged),
                        decision=request_body["decision_id"],
                    ),
                )
        checks.append("real_sql_refuses_mechanics_binding_reclassified_to_production_namespace")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ai.stockout_model_versions WHERE model_name=:model"),
                    dict(model=MODEL),
                )
                == 0
            )
        with journal.locked(TEST_MODEL):
            active = journal.active(TEST_MODEL)
            assert not journal.pending(TEST_MODEL)
            assert active.release_id == final["release_id"]
        state_file.write_bytes(
            canonical_bytes(
                dict(
                    version="stockout-postgres-lifecycle-acceptance-1.0.0",
                    status="restart_pending",
                    purpose="stockout_database_mechanics_only",
                    checks=checks,
                    model_name=TEST_MODEL,
                    model_version=v3,
                    release_id=active.release_id,
                    migration_revision=EXPECTED_REVISION,
                    real_postgres=True,
                    real_mlflow_stockout_registry=True,
                    independent_quality_accepted=False,
                    final_test_outcomes_evaluated=False,
                    production_model_promoted=False,
                    ai08_ready=False,
                )
            )
            + b"\n"
        )
    finally:
        engine.dispose()
