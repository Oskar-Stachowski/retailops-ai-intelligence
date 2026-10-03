"""Disposable-only backup proof: an unfinished real registration survives restore."""

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from retailops_ai.config import load_settings
from retailops_ai.model_lifecycle.acceptance import require, source
from retailops_ai.model_lifecycle.contracts import TEST_MODEL, Request
from retailops_ai.model_lifecycle.engine import Lifecycle
from retailops_ai.model_lifecycle.journal import PostgresJournal
from retailops_ai.model_lifecycle.mlflow import MLflowRegistry
from retailops_ai.security.model_operator import model_operator
from retailops_ai.security.provision import provision

DECISION = "decision-mechanics-register-04"


def acceptance(*, prepare: bool, inspect: bool = False) -> dict[str, Any]:
    settings = load_settings()
    if settings.app_env != "test" or settings.database_url is None:
        raise ValueError("store_acceptance_requires_test_database")
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    journal = PostgresJournal(engine)
    client = MLflowRegistry(compose=True, environment="test")
    try:
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
            grants.chmod(0o600)
            provision(grants, private / "access", 1)
            actor = model_operator(
                private / "access/api-access-policy.json",
                private / "access/api-client-credentials.json",
            )
            if prepare:
                run, checksum = source(client, 4.0, "mechanics-source-04")
                request = Request(
                    action="register",
                    model_name="retailops-demand-forecast-mechanics",
                    decision_id=DECISION,
                    mlflow_run_id=run,
                    evidence_id="mechanics-source-04",
                    qualification_sha256=checksum,
                    reason="Registration interrupted before combined backup",
                )

                class LostResponse(MLflowRegistry):
                    def create(
                        self, model: str, run_id: str, uri: str, decision: str, digest: str
                    ) -> str:
                        super().create(model, run_id, uri, decision, digest)
                        raise TimeoutError("simulated_lost_response_before_backup")

                try:
                    Lifecycle(
                        LostResponse(compose=True, environment="test"), journal, environment="test"
                    ).execute(request, actor)
                except TimeoutError:
                    pass
                else:
                    raise ValueError("store_failure_injection_missing")
                with journal.locked(TEST_MODEL):
                    require(journal.pending(TEST_MODEL) == [DECISION], "pending_decision_missing")
                require(client.find(TEST_MODEL, DECISION) == ["4"], "pending_version_missing")
                return {"status": "prepared", "pending_decision": DECISION, "version": "4"}
            with journal.locked(TEST_MODEL):
                require(
                    journal.pending(TEST_MODEL) == ([] if inspect else [DECISION]),
                    "restored_decision_state_mismatch",
                )
                record = journal.decision(DECISION)
                require(record is not None, "pending_record_missing")
                if record is None:
                    raise ValueError("pending_record_missing")
                request = Request.model_validate_json(json.dumps(record["request"]))
                previous = journal.active(TEST_MODEL)
            lifecycle = Lifecycle(client, journal, environment="test")
            result = lifecycle.execute(request, actor)
            require(
                result["model_version"] == "4" and result["replayed"] is inspect,
                "restored_recovery_failed",
            )
            require(lifecycle.execute(request, actor)["replayed"] is True, "restored_replay_failed")
            require(
                client.find(TEST_MODEL, DECISION) == ["4"], "restored_recovery_created_duplicate"
            )
            with journal.locked(TEST_MODEL):
                require(journal.active(TEST_MODEL) == previous, "recovery_changed_approved_release")
                require(not journal.pending(TEST_MODEL), "restored_decision_still_pending")
                require(journal.rejected(TEST_MODEL, "3"), "restored_rejection_missing")
                for version in ("1", "2", "3", "4"):
                    client.validate(journal.binding(TEST_MODEL, version))
                try:
                    journal.query(
                        "UPDATE ai.model_decisions SET record=record WHERE decision_id='decision-mechanics-promote-01'"
                    )
                except IntegrityError as exc:
                    require(
                        getattr(exc.orig, "sqlstate", None) == "23514",
                        "restored_audit_trigger_wrong_error",
                    )
                    if journal.connection is None:
                        raise ValueError("journal_connection_missing") from None
                    journal.connection.rollback()
                else:
                    raise ValueError("restored_audit_update_was_allowed")
            require(
                client.aliases(TEST_MODEL) == {"candidate": "4", "champion": "1", "rollback": "2"},
                "restored_recovery_aliases_mismatch",
            )
            if previous is None:
                raise ValueError("restored_release_missing")
            return {
                "status": "passed",
                "release_id": previous.release_id,
                "checks": [
                    "restored_pending_registration_resumes_without_duplicate_version",
                    "restored_decision_replay_retains_approved_release_and_rejection",
                    "all_four_restored_capsules_pass_checksum_and_load",
                    "restored_append_only_audit_trigger_blocks_update",
                ],
            }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(acceptance(prepare=args.prepare, inspect=args.inspect), sort_keys=True))
        return 0
    except Exception:
        print('{"error":"lifecycle_store_acceptance_failed"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
