"""Real PostgreSQL pointer acceptance on approved synthetic test corpus only."""

import json
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError

from retailops_ai.adapters import index_lifecycle as lifecycle
from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.adapters.vector_store import index_engine, read_candidate, store_candidate
from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.indexes import IndexCandidate
from retailops_ai.knowledge.releases import CorpusApproval, IndexPin, SwitchRequest
from retailops_ai.pipelines.releases import validate_candidate


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def synthetic_approval(candidate: IndexCandidate) -> CorpusApproval:
    corpus = candidate.chunks.corpus
    value = {
        "schema_version": "1.0",
        "corpus_id": corpus.corpus_id,
        "corpus_config_id": corpus.corpus_config_id,
        "environment": "test",
        "review_owner": corpus.review_owner,
        "reviewer": "fixture-review-pipeline",
        "reviewer_kind": "approved_pipeline",
        "decision": "approved",
        "scope": "sources_status_access_and_exclusions",
        "reviewed_at": "2026-09-28T10:00:00Z",
    }
    value["review_id"] = "corpus-review-sha256-" + canonical_sha256(value)
    return CorpusApproval.model_validate_json(json.dumps(value))


def verify_lifecycle(candidates: list[IndexCandidate]) -> dict[str, object]:
    a, b, c, d, _ = candidates
    engine = index_engine(load_settings())
    observer = index_engine(load_settings())
    checks: list[str] = []

    def current() -> IndexPin | None:
        return lifecycle.current_index(observer, "test", "offline_test")

    def request(
        candidate: IndexCandidate, generation: int, operation: str = "activate"
    ) -> SwitchRequest:
        return SwitchRequest.model_validate(
            {
                "schema_version": "1.0",
                "request_id": "rag-change-" + uuid4().hex,
                "environment": "test",
                "lane": "offline_test",
                "target_index_id": candidate.manifest.index_id,
                "expected_generation": generation,
                "actor": "fixture-promoter",
                "operation": operation,
            }
        )

    def rejected(request: SwitchRequest, code: str) -> None:
        before = current()
        try:
            lifecycle.switch_index(engine, request)
        except CorpusError as exc:
            require(str(exc) == code, "unexpected_lifecycle_rejection")
        else:
            raise RuntimeError("invalid_activation_accepted")
        require(current() == before, "failed_activation_changed_pointer")

    def count_events() -> int:
        with engine.connect() as connection:
            return int(connection.scalar(text("SELECT count(*) FROM ai.rag_index_changes")) or 0)

    try:
        for candidate in candidates:
            store_candidate(engine, candidate)
        approvals = {
            candidate.manifest.index_id: synthetic_approval(candidate) for candidate in candidates
        }
        for candidate in (a, b, c):
            review = approvals[candidate.manifest.index_id]
            report = validate_candidate(candidate)
            lifecycle.qualify_index(
                engine, candidate.manifest.index_id, "test", "offline_test", review, report
            )
            require(
                not lifecycle.qualify_index(
                    engine, candidate.manifest.index_id, "test", "offline_test", review, report
                ),
                "qualification_replay_changed",
            )
        before = current()
        base_generation = before.generation if before else 0
        first = request(a, base_generation)
        applied = lifecycle.switch_index(engine, first)
        require(
            applied.status == "applied" and applied.pin.manifest == a.manifest,
            "initial_activation_failed",
        )
        pinned = applied.pin
        generation = pinned.generation
        count = count_events()
        replay = lifecycle.switch_index(engine, first)
        require(
            replay.status == "replayed" and replay.pin == pinned and count_events() == count,
            "activation_retry_not_idempotent",
        )
        wrong = first.model_copy(update={"target_index_id": b.manifest.index_id})
        rejected(wrong, "activation_request_reuse_mismatch")
        rejected(request(d, generation), "index_not_qualified")
        rejected(request(b, generation - 1), "active_generation_conflict")
        rejected(request(c, generation, "rollback"), "rollback_history_missing")
        rejected(
            request(b, generation).model_copy(update={"lane": "retrieval"}),
            "golden_evaluation_required",
        )
        rejected(
            request(b, generation).model_copy(update={"environment": "local"}),
            "offline_activation_requires_test_environment",
        )
        checks.append("qualification_replay_generation_cas_unqualified_and_golden_gate_rejections")

        saved_writer = lifecycle._write_pointer

        def crash(connection: Connection, request: SwitchRequest, generation: int) -> None:
            saved_writer(connection, request, generation)
            raise RuntimeError("injected_pointer_failure")

        lifecycle._write_pointer = crash
        count = count_events()
        try:
            lifecycle.switch_index(engine, request(b, generation))
        except RuntimeError as exc:
            require(str(exc) == "injected_pointer_failure", "unexpected_pointer_failure")
        else:
            raise RuntimeError("pointer_failure_not_injected")
        finally:
            lifecycle._write_pointer = saved_writer
        require(
            current() == pinned and count_events() == count,
            "failed_swap_left_partial_pointer_or_event",
        )
        checks.append("failure_after_pointer_write_rolls_back_event_and_pointer")

        next_request = request(b, generation)
        with engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO ai.rag_index_changes(request_id,environment,lane,generation,previous_index_id,index_id,request)
                VALUES (:request,'test','offline_test',:generation,:previous,:index,CAST(:payload AS jsonb))"""),
                {
                    "request": next_request.request_id,
                    "generation": generation + 1,
                    "previous": a.manifest.index_id,
                    "index": b.manifest.index_id,
                    "payload": next_request.model_dump_json(),
                },
            )
            require(current() == pinned, "reader_saw_uncommitted_change_event")
            lifecycle._write_pointer(connection, next_request, generation + 1)
            require(current() == pinned, "reader_saw_uncommitted_pointer")
        switched = current()
        require(
            switched is not None and switched.manifest == b.manifest,
            "reader_did_not_see_committed_swap",
        )
        require(pinned.manifest == a.manifest, "pinned_request_changed_version")
        with engine.connect() as connection:
            require(
                read_candidate(connection, pinned.manifest.index_id) == a,
                "pinned_old_candidate_changed",
            )
        # Retry after a later swap returns the historical receipt, not the live pointer.
        replay = lifecycle.switch_index(engine, first)
        require(
            replay.pin == pinned and current() == switched, "historical_retry_changed_live_pointer"
        )
        checks.append("readers_see_old_until_commit_and_pinned_run_retains_old_manifest")
        rollback = lifecycle.switch_index(engine, request(a, generation + 1, "rollback"))
        require(rollback.pin.manifest == a.manifest, "rollback_did_not_restore_original_manifest")
        generation = rollback.pin.generation
        checks.append("rollback_restores_previous_manifest_with_new_generation")

        requests = [request(b, generation), request(c, generation)]

        def competing(request: SwitchRequest) -> str:
            independent = index_engine(load_settings())
            try:
                try:
                    lifecycle.switch_index(independent, request)
                    return "applied"
                except CorpusError as exc:
                    return str(exc)
            finally:
                independent.dispose()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(competing, requests))
        require(
            sorted(results) == ["active_generation_conflict", "applied"],
            "concurrent_promoters_overwrote_each_other",
        )
        concurrent_pin = current()
        require(
            concurrent_pin is not None and concurrent_pin.generation == generation + 1,
            "concurrent_generation_invalid",
        )
        checks.append("two_independent_promoters_have_one_winner_and_one_generation_conflict")

        with engine.connect() as connection:
            pin = lifecycle.read_current(connection, "test", "offline_test")
        if pin is None:
            raise RuntimeError("active_pin_missing")
        for statement in (
            "UPDATE ai.rag_active_indexes SET generation=generation+1 WHERE environment='test' AND lane='offline_test'",
            "DELETE FROM ai.rag_active_indexes WHERE environment='test' AND lane='offline_test'",
            "DELETE FROM ai.rag_index_changes",
        ):
            try:
                with engine.begin() as connection:
                    connection.execute(text(statement))
            except DBAPIError as exc:
                require(
                    getattr(exc.orig, "sqlstate", None) == "23514", "unexpected_pointer_constraint"
                )
            else:
                raise RuntimeError("invalid_direct_pointer_mutation_accepted")
        orphan = request(a, pin.generation)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("""INSERT INTO ai.rag_index_changes(request_id,environment,lane,generation,previous_index_id,index_id,request)
                    VALUES (:request,'test','offline_test',:generation,:previous,:index,CAST(:payload AS jsonb))"""),
                    {
                        "request": orphan.request_id,
                        "generation": pin.generation + 1,
                        "previous": pin.manifest.index_id,
                        "index": a.manifest.index_id,
                        "payload": orphan.model_dump_json(),
                    },
                )
        except DBAPIError as exc:
            require(
                getattr(exc.orig, "sqlstate", None) == "23514", "unexpected_orphan_change_failure"
            )
        else:
            raise RuntimeError("orphan_change_event_committed")
        require(current() == pin, "direct_sql_failure_changed_pointer")
        checks.append("sql_blocks_incomplete_history_generation_jump_and_deletion")

        cli = shutil.which("retailops-ai")
        if cli is None:
            raise RuntimeError("lifecycle_cli_unavailable")
        environment = {**os.environ, "APP_ENV": "test"}
        with tempfile.TemporaryDirectory() as directory:
            approval_path = Path(directory) / "approval.json"
            validation_path = Path(directory) / "validation.json"
            approval_path.write_text(approvals[a.manifest.index_id].model_dump_json())
            validation_path.write_text(validate_candidate(a).model_dump_json())
            result = subprocess.run(  # noqa: S603 - fixed installed CLI and synthetic artifacts
                [
                    cli,
                    "index-qualify",
                    "--index-id",
                    a.manifest.index_id,
                    "--lane",
                    "offline_test",
                    "--approval",
                    str(approval_path),
                    "--validation",
                    str(validation_path),
                ],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )  # noqa: S603 - fixed installed CLI and synthetic artifacts
            require(
                result.returncode == 0
                and json.loads(result.stdout)["status"] == "already_qualified",
                "qualification_cli_failed",
            )
        final_request = request(a, pin.generation, "rollback")
        args = [
            cli,
            "index-rollback",
            "--index-id",
            a.manifest.index_id,
            "--expected-generation",
            str(pin.generation),
            "--request-id",
            final_request.request_id,
            "--actor",
            final_request.actor,
            "--lane",
            "offline_test",
        ]
        result = subprocess.run(args, env=environment, text=True, capture_output=True, check=False)  # noqa: S603 - controlled test rollback
        require(result.returncode == 0, "rollback_cli_failed")
        expected = json.loads(result.stdout)["pin"]
        result = subprocess.run(  # noqa: S603 - controlled read of test lane
            [cli, "index-current", "--lane", "offline_test"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )  # noqa: S603 - controlled read of test lane
        require(
            result.returncode == 0 and json.loads(result.stdout)["pin"] == expected,
            "current_cli_pin_mismatch",
        )
        args.remove("offline_test")
        args.remove("--lane")
        result = subprocess.run(args, env=environment, text=True, capture_output=True, check=False)  # noqa: S603 - default retrieval lane must reject
        require(
            result.returncode == 2 and "fixture-promoter" not in result.stderr,
            "retrieval_cli_bypassed_golden_gate",
        )
        checks.append("real_cli_qualify_rollback_current_and_default_retrieval_denial")
        require(
            lifecycle.current_index(engine, "local", "retrieval") is None,
            "real_local_index_activated",
        )
        return {
            "result": "passed",
            "checks": checks,
            "pin": expected,
            "base_generation": base_generation,
            "actual_corpus_activated": False,
            "semantic_quality_evaluated": False,
        }
    finally:
        observer.dispose()
        engine.dispose()
