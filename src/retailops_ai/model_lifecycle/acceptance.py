"""Real PostgreSQL/MLflow mechanics in a disposable Compose project only."""

import argparse
import hashlib
import http.client
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from retailops_ai.config import load_settings
from retailops_ai.model_lifecycle.contracts import TEST_MODEL, Request
from retailops_ai.model_lifecycle.engine import Lifecycle
from retailops_ai.model_lifecycle.journal import PostgresJournal
from retailops_ai.model_lifecycle.mechanics import capsule
from retailops_ai.model_lifecycle.mlflow import MLflowRegistry
from retailops_ai.security.model_operator import model_operator
from retailops_ai.security.provision import provision


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def source(client: MLflowRegistry, bias: float, evidence: str) -> tuple[str, str]:
    _, files = capsule(bias, evidence)
    digest = hashlib.sha256(files["qualification.json"]).hexdigest()
    experiment = client.api("experiments/create", {"name": evidence})["experiment_id"]
    run = client.api(
        "runs/create",
        {
            "experiment_id": experiment,
            "start_time": int(time.time() * 1000),
            "tags": [{"key": "retailops.qualification_sha256", "value": digest}],
        },
    )["run"]
    for name, raw in files.items():
        path = (
            "/api/2.0/mlflow-artifacts/artifacts/"
            + run["info"]["artifact_uri"].removeprefix("mlflow-artifacts:/").lstrip("/")
            + "/lifecycle/"
            + name
        )
        connection = http.client.HTTPConnection(client.host, client.port, timeout=30)
        try:
            connection.request("PUT", path, raw, {"Content-Type": "application/octet-stream"})
            response = connection.getresponse()
            response.read(1024)
            require(response.status == 200, "mechanics_artifact_upload_failed")
        finally:
            connection.close()
    client.api(
        "runs/update",
        {
            "run_id": run["info"]["run_id"],
            "status": "FINISHED",
            "end_time": int(time.time() * 1000),
        },
    )
    return str(run["info"]["run_id"]), digest


def run_acceptance(*, inspect: bool) -> dict[str, Any]:
    settings = load_settings()
    if settings.app_env != "test" or settings.database_url is None:
        raise ValueError("mechanics_requires_disposable_test_database")
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    client = MLflowRegistry(compose=True, environment="test")
    journal = PostgresJournal(engine)
    checks: list[str] = []
    try:
        if inspect:
            with journal.locked(TEST_MODEL):
                active = journal.active(TEST_MODEL)
                require(active is not None, "approved_release_missing_after_restart")
                if active is None:
                    raise ValueError("approved_release_missing")
                require(active.binding.model_version == "1", "release_changed_after_restart")
                require(
                    client.aliases(TEST_MODEL)
                    == {"candidate": "3", "champion": "1", "rollback": "2"},
                    "aliases_changed_after_restart",
                )
                for version in ("1", "2", "3"):
                    client.validate(journal.binding(TEST_MODEL, version))
                require(journal.rejected(TEST_MODEL, "3"), "rejection_lost_after_restart")
                require(not journal.pending(TEST_MODEL), "unexpected_incomplete_decision")
                # Administrative updates are not part of the runtime's ability to update history.
                try:
                    journal.query(
                        "UPDATE ai.model_decisions SET record=record WHERE decision_id='decision-mechanics-promote-01'"
                    )
                except IntegrityError as exc:
                    require(
                        getattr(exc.orig, "sqlstate", None) == "23514",
                        "unexpected_audit_update_failure",
                    )
                    if journal.connection is None:
                        raise ValueError("journal_connection_missing") from None
                    journal.connection.rollback()
                    checks.append("append_only_decision_trigger_blocks_update")
                else:
                    raise ValueError("model_audit_update_was_allowed")
                checks.append("sigkill_restart_retains_versions_artifacts_decisions_and_release")
                return {
                    "status": "passed",
                    "purpose": "lifecycle_mechanics_only",
                    "checks": checks,
                    "release_id": active.release_id,
                }
        with engine.connect() as connection:
            require(
                connection.scalar(text("SELECT count(*) FROM ai.model_decisions")) == 0,
                "fresh_model_journal_required",
            )
        require(client.aliases(TEST_MODEL) == {}, "fresh_test_registry_required")
        with tempfile.TemporaryDirectory(prefix="model-operator-") as temporary:
            private = Path(temporary)
            grants = private / "grants.json"
            grants.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "policy_id": "mechanics-promoter",
                        "grants": [
                            {
                                "principal_id": "mechanics-promoter",
                                "roles": ["promoter"],
                                "capabilities": ["model:decide"],
                                "scope": None,
                                "knowledge_scope": None,
                            }
                        ],
                    }
                )
            )
            provision(grants, private / "access", 1)
            actor = model_operator(
                private / "access/api-access-policy.json",
                private / "access/api-client-credentials.json",
            )
            lifecycle = Lifecycle(client, journal, environment="test")
            sources: dict[str, tuple[str, str]] = {}

            def request(action: str, version: str) -> Request:
                run, digest = sources[version]
                return Request.model_validate_json(
                    json.dumps(
                        {
                            "action": action,
                            "model_name": TEST_MODEL,
                            "decision_id": "decision-mechanics-" + action + "-0" + version,
                            "mlflow_run_id": run if action == "register" else None,
                            "model_version": version if action != "register" else None,
                            "evidence_id": "mechanics-source-0" + version,
                            "qualification_sha256": digest,
                            "reason": "Controlled lifecycle mechanics acceptance",
                            "image_digest": "sha256:" + version * 64
                            if action == "promote"
                            else None,
                        }
                    )
                )

            sources["1"] = source(client, 1.0, "mechanics-source-01")
            require(
                lifecycle.execute(request("register", "1"), actor)["model_version"] == "1",
                "first_version_not_one",
            )
            promote1 = request("promote", "1")
            lifecycle.execute(promote1, actor)
            with journal.locked(TEST_MODEL):
                try:
                    with PostgresJournal(engine).locked(TEST_MODEL):
                        raise RuntimeError("concurrent_model_lock_was_allowed")
                except ValueError as exc:
                    require(str(exc) == "model_lifecycle_busy", "unexpected_model_lock_failure")
            checks.append("database_lock_serializes_independent_operator_sessions")
            with journal.locked(TEST_MODEL):
                first = journal.active(TEST_MODEL)
                require(
                    first is not None
                    and first.previous_version is None
                    and first.previous_release_id is None,
                    "first_release_previous_must_be_null",
                )
            if first is None:
                raise ValueError("first_release_missing")
            checks.append("first_release_has_explicit_null_previous_version")
            sources["2"] = source(client, 2.0, "mechanics-source-02")

            class LostCreateResponse(MLflowRegistry):
                def create(
                    self, model: str, run_id: str, uri: str, decision: str, digest: str
                ) -> str:
                    super().create(model, run_id, uri, decision, digest)
                    raise TimeoutError("simulated_lost_create_response")

            try:
                Lifecycle(
                    LostCreateResponse(compose=True, environment="test"),
                    journal,
                    environment="test",
                ).execute(request("register", "2"), actor)
            except TimeoutError:
                pass
            else:
                raise ValueError("create_failure_injection_missing")
            require(
                lifecycle.execute(request("register", "2"), actor)["model_version"] == "2",
                "create_recovery_wrong_version",
            )
            require(
                client.find(TEST_MODEL, request("register", "2").decision_id) == ["2"],
                "duplicate_version_after_recovery",
            )
            checks.append("lost_registration_response_recovers_without_duplicate_version")

            class LostAliasResponse(MLflowRegistry):
                def set_alias(self, model: str, alias: str, version: str) -> None:
                    super().set_alias(model, alias, version)
                    raise TimeoutError("simulated_lost_alias_response")

            promote2 = request("promote", "2")
            try:
                Lifecycle(
                    LostAliasResponse(compose=True, environment="test"), journal, environment="test"
                ).execute(promote2, actor)
            except TimeoutError:
                pass
            else:
                raise ValueError("alias_failure_injection_missing")
            with journal.locked(TEST_MODEL):
                require(
                    journal.active(TEST_MODEL) == first,
                    "partial_promotion_changed_approved_release",
                )
            require(
                client.aliases(TEST_MODEL).get("rollback") == "1",
                "known_previous_version_not_retained",
            )
            lifecycle.execute(promote2, actor)
            checks.append("partial_alias_change_resumes_from_actual_registry_state")
            require(
                lifecycle.execute(promote1, actor)["replayed"] is True, "old_promotion_not_replayed"
            )
            require(
                client.aliases(TEST_MODEL).get("champion") == "2",
                "old_replay_reverted_later_release",
            )
            with journal.locked(TEST_MODEL):
                require(journal.release(first.release_id) == first, "prior_release_pins_changed")
            checks.append("old_decision_replay_and_alias_changes_do_not_rewrite_pins")
            lifecycle.execute(request("rollback", "1"), actor)
            with journal.locked(TEST_MODEL):
                restored = journal.active(TEST_MODEL)
                require(
                    restored is not None
                    and restored.binding == first.binding
                    and restored.image_digest == first.image_digest
                    and restored.restored_from_release_id == first.release_id,
                    "rollback_did_not_restore_exact_model_and_image",
                )
            checks.append("rollback_restores_exact_model_checksum_config_schema_and_image")
            sources["3"] = source(client, 3.0, "mechanics-source-03")
            lifecycle.execute(request("register", "3"), actor)
            lifecycle.execute(request("reject", "3"), actor)
            try:
                lifecycle.execute(request("promote", "3"), actor)
            except ValueError as exc:
                require(
                    str(exc) == "rejected_model_cannot_be_released", "unexpected_rejection_failure"
                )
            else:
                raise ValueError("rejected_model_was_promoted")
            require(
                client.aliases(TEST_MODEL) == {"candidate": "3", "champion": "1", "rollback": "2"},
                "reject_changed_champion",
            )
            checks.append("rejected_candidate_cannot_replace_champion")
            with journal.locked(TEST_MODEL):
                active = journal.active(TEST_MODEL)
                require(active is not None, "final_release_missing")
                if active is None:
                    raise ValueError("approved_release_missing")
                return {
                    "status": "passed",
                    "purpose": "lifecycle_mechanics_only",
                    "model_name": TEST_MODEL,
                    "checks": checks,
                    "release_id": active.release_id,
                    "versions": ["1", "2", "3"],
                    "sources": {
                        version: {"run_id": value[0], "qualification_sha256": value[1]}
                        for version, value in sources.items()
                    },
                    "runtime_status": "not_integrated",
                    "forecast_quality_approved": False,
                }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run_acceptance(inspect=args.inspect), sort_keys=True))
        return 0
    except Exception:
        print('{"error":"model_lifecycle_acceptance_failed"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
