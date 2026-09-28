"""Serialize qualification and pointer changes; pin one immutable version per read."""

import json

from sqlalchemy import Connection, Engine, text

from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.adapters.vector_store import STORE_LOCK_ID, _boundary, read_candidate
from retailops_ai.knowledge.indexes import IndexCandidate
from retailops_ai.knowledge.releases import (
    CorpusApproval,
    IndexPin,
    IndexValidation,
    Lane,
    SwitchRequest,
    SwitchResult,
    approval_matches,
)
from retailops_ai.pipelines.releases import validate_candidate


def _transaction(connection: Connection) -> None:
    connection.execute(text("SET LOCAL statement_timeout='15s'"))
    connection.execute(text("SET LOCAL lock_timeout='3s'"))
    _boundary(connection)
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": STORE_LOCK_ID})


def _candidate(connection: Connection, index_id: str, environment: str) -> IndexCandidate:
    candidate = read_candidate(connection, index_id)
    if candidate is None:
        raise CorpusError("index_not_found")
    if candidate.manifest.environment != environment:
        raise CorpusError("index_environment_mismatch")
    return candidate


def qualify_index(
    engine: Engine,
    index_id: str,
    environment: str,
    lane: Lane,
    approval: CorpusApproval,
    validation: IndexValidation,
) -> bool:
    # Retrieval never consumes fake mechanical acceptance as a golden quality report.
    if lane != "offline_test":
        raise CorpusError("golden_evaluation_required")
    if environment != "test":
        raise CorpusError("offline_activation_requires_test_environment")
    approval = CorpusApproval.model_validate_json(approval.model_dump_json())
    validation = IndexValidation.model_validate_json(validation.model_dump_json())
    with engine.begin() as connection:
        _transaction(connection)
        candidate = _candidate(connection, index_id, environment)
        corpus = candidate.chunks.corpus
        if not approval_matches(
            approval, corpus.corpus_id, corpus.corpus_config_id, corpus.review_owner, environment
        ):
            raise CorpusError("corpus_review_binding_mismatch")
        expected = validate_candidate(candidate)
        if validation != expected or validation.result != "passed":
            raise CorpusError("index_validation_failed")
        existing = connection.execute(
            text(
                "SELECT review_id, validation FROM ai.rag_qualifications "
                "WHERE index_id=:id AND environment=:env AND lane=:lane"
            ),
            {"id": index_id, "env": environment, "lane": lane},
        ).first()
        if existing is not None:
            if (
                existing.review_id != approval.review_id
                or existing.validation != validation.model_dump(mode="json")
            ):
                raise CorpusError("qualification_replay_mismatch")
            return False
        connection.execute(
            text("""INSERT INTO ai.rag_corpus_reviews(review_id,environment,corpus_id,approval)
            VALUES (:review,:env,:corpus,CAST(:approval AS jsonb)) ON CONFLICT DO NOTHING"""),
            {
                "review": approval.review_id,
                "env": environment,
                "corpus": corpus.corpus_id,
                "approval": approval.model_dump_json(),
            },
        )
        stored_approval = connection.scalar(
            text("SELECT approval FROM ai.rag_corpus_reviews WHERE review_id=:review"),
            {"review": approval.review_id},
        )
        if stored_approval != approval.model_dump(mode="json"):
            raise CorpusError("corpus_review_collision")
        connection.execute(
            text("""INSERT INTO ai.rag_qualifications(index_id,environment,lane,review_id,validation_id,validation)
            VALUES (:id,:env,:lane,:review,:validation,CAST(:payload AS jsonb))"""),
            {
                "id": index_id,
                "env": environment,
                "lane": lane,
                "review": approval.review_id,
                "validation": validation.validation_id,
                "payload": validation.model_dump_json(),
            },
        )
    return True


def _pin(connection: Connection, request_id: str) -> IndexPin:
    row = (
        connection.execute(
            text("""SELECT e.environment,e.lane,e.generation,e.request_id,
        q.review_id,q.validation_id,i.manifest FROM ai.rag_index_changes e
        JOIN ai.rag_qualifications q USING(index_id,environment,lane)
        JOIN ai.rag_indexes i ON i.index_id=e.index_id WHERE e.request_id=:request"""),
            {"request": request_id},
        )
        .mappings()
        .one()
    )
    return IndexPin.model_validate_json(
        json.dumps({"schema_version": "1.0", "purpose": "lifecycle_validation_only", **dict(row)})
    )


def read_current(connection: Connection, environment: str, lane: Lane) -> IndexPin | None:
    # One statement snapshot binds pointer, event, qualification and manifest.
    row = (
        connection.execute(
            text("""SELECT a.environment,a.lane,a.generation,a.request_id,
        q.review_id,q.validation_id,i.manifest FROM ai.rag_active_indexes a
        JOIN ai.rag_qualifications q USING(index_id,environment,lane)
        JOIN ai.rag_indexes i ON i.index_id=a.index_id WHERE a.environment=:env AND a.lane=:lane"""),
            {"env": environment, "lane": lane},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return IndexPin.model_validate_json(
        json.dumps({"schema_version": "1.0", "purpose": "lifecycle_validation_only", **dict(row)})
    )


def current_index(engine: Engine, environment: str, lane: Lane) -> IndexPin | None:
    with engine.connect() as connection:
        _boundary(connection)
        return read_current(connection, environment, lane)


def _write_pointer(connection: Connection, request: SwitchRequest, generation: int) -> None:
    values = {
        "env": request.environment,
        "lane": request.lane,
        "generation": generation,
        "request": request.request_id,
        "id": request.target_index_id,
        "expected": request.expected_generation,
    }
    if generation == 1:
        connection.execute(
            text("""INSERT INTO ai.rag_active_indexes(environment,lane,generation,request_id,index_id)
            VALUES (:env,:lane,:generation,:request,:id)"""),
            values,
        )
    else:
        result = connection.execute(
            text("""UPDATE ai.rag_active_indexes SET generation=:generation,request_id=:request,index_id=:id
            WHERE environment=:env AND lane=:lane AND generation=:expected"""),
            values,
        )
        if result.rowcount != 1:
            raise CorpusError("active_generation_conflict")


def switch_index(engine: Engine, request: SwitchRequest) -> SwitchResult:
    request = SwitchRequest.model_validate_json(request.model_dump_json())
    if request.lane != "offline_test":
        raise CorpusError("golden_evaluation_required")
    if request.environment != "test":
        raise CorpusError("offline_activation_requires_test_environment")
    with engine.begin() as connection:
        _transaction(connection)
        replay = connection.scalar(
            text("SELECT request FROM ai.rag_index_changes WHERE request_id=:request"),
            {"request": request.request_id},
        )
        if replay is not None:
            if replay != request.model_dump(mode="json"):
                raise CorpusError("activation_request_reuse_mismatch")
            return SwitchResult(status="replayed", pin=_pin(connection, request.request_id))
        previous = read_current(connection, request.environment, request.lane)
        generation = previous.generation if previous else 0
        if generation != request.expected_generation:
            raise CorpusError("active_generation_conflict")
        _candidate(connection, request.target_index_id, request.environment)
        qualified = connection.scalar(
            text(
                "SELECT 1 FROM ai.rag_qualifications WHERE index_id=:id AND environment=:env AND lane=:lane"
            ),
            {"id": request.target_index_id, "env": request.environment, "lane": request.lane},
        )
        if qualified != 1:
            raise CorpusError("index_not_qualified")
        if previous and previous.manifest.index_id == request.target_index_id:
            raise CorpusError("target_already_active")
        if request.operation == "rollback" and not connection.scalar(
            text(
                "SELECT 1 FROM ai.rag_index_changes "
                "WHERE environment=:env AND lane=:lane AND index_id=:id LIMIT 1"
            ),
            {"id": request.target_index_id, "env": request.environment, "lane": request.lane},
        ):
            raise CorpusError("rollback_history_missing")
        connection.execute(
            text("""INSERT INTO ai.rag_index_changes(request_id,environment,lane,generation,previous_index_id,index_id,request)
            VALUES (:request,:env,:lane,:generation,:previous,:id,CAST(:payload AS jsonb))"""),
            {
                "request": request.request_id,
                "env": request.environment,
                "lane": request.lane,
                "generation": generation + 1,
                "previous": previous.manifest.index_id if previous else None,
                "id": request.target_index_id,
                "payload": request.model_dump_json(),
            },
        )
        _write_pointer(connection, request, generation + 1)
        pin = _pin(connection, request.request_id)
    return SwitchResult(status="applied", pin=pin)
