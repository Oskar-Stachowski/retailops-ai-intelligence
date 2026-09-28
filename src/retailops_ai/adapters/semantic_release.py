"""Promote only a persisted, successful semantic job after offline reproduction."""

import json

from sqlalchemy import Engine, text

from retailops_ai.adapters.embedding_snapshot import SnapshotEmbeddingProvider
from retailops_ai.adapters.index_lifecycle import _candidate, _transaction
from retailops_ai.knowledge.jobs import (
    BUILD_PROFILE_ADAPTER,
    RUN_REPORT_ADAPTER,
    GoldenIndexRunReport,
    SemanticIndexBuildProfile,
)
from retailops_ai.pipelines.index_builds import check_build_profile
from retailops_ai.pipelines.qualification import prepare_release


def qualify_semantic_run(engine: Engine, environment: str, run_id: str) -> bool:
    with engine.begin() as connection:
        _transaction(connection)
        row = connection.execute(
            text("""SELECT k.status,k.output_index_id,r.report,p.profile
            FROM ai.knowledge_index_runs k
            JOIN ai.rag_index_reports r ON r.report_id=k.report_id AND r.profile_id=k.profile_id
            JOIN ai.rag_build_profiles p ON p.profile_id=k.profile_id
            WHERE k.run_id=:run AND k.environment=:env"""),
            {"run": run_id, "env": environment},
        ).first()
        if row is None or row.status != "succeeded":
            raise ValueError("successful_semantic_run_required")
        profile = BUILD_PROFILE_ADAPTER.validate_json(json.dumps(row.profile))
        report = RUN_REPORT_ADAPTER.validate_json(json.dumps(row.report))
        if (
            not isinstance(profile, SemanticIndexBuildProfile)
            or not isinstance(report, GoldenIndexRunReport)
            or not report.quality_gate_passed
        ):
            raise ValueError("successful_semantic_run_required")
        candidate = _candidate(connection, row.output_index_id, environment)
        check_build_profile(profile, candidate)
        release = prepare_release(
            candidate,
            profile.golden_set,
            profile.retrieval_config,
            report.golden,
            profile.similarity_policy,
            corpus_approval=profile.approval,
            golden_approval=profile.golden_approval,
            similarity_review=profile.similarity_review,
            provider=SnapshotEmbeddingProvider(profile.embedding_config, profile.query_embeddings),
        )
        if not release.activation_allowed or release.mechanical_validation != report.validation:
            raise ValueError("semantic_release_gate_failed")
        existing = connection.scalar(
            text(
                "SELECT release_id FROM ai.rag_qualifications WHERE index_id=:id AND environment=:env AND lane='retrieval'"
            ),
            {"id": candidate.manifest.index_id, "env": environment},
        )
        if existing is not None:
            if existing != release.release_id:
                raise ValueError("semantic_qualification_replay_mismatch")
            return False
        approval = profile.approval
        connection.execute(
            text("""INSERT INTO ai.rag_corpus_reviews(review_id,environment,corpus_id,approval)
            VALUES (:id,:env,:corpus,CAST(:approval AS jsonb)) ON CONFLICT DO NOTHING"""),
            {
                "id": approval.review_id,
                "env": environment,
                "corpus": approval.corpus_id,
                "approval": approval.model_dump_json(),
            },
        )
        stored = connection.scalar(
            text("SELECT approval FROM ai.rag_corpus_reviews WHERE review_id=:id"),
            {"id": approval.review_id},
        )
        if stored != approval.model_dump(mode="json"):
            raise ValueError("corpus_review_collision")
        connection.execute(
            text("""INSERT INTO ai.rag_semantic_releases(release_id,index_id,environment,report_id,release)
            VALUES (:id,:index,:env,:report,CAST(:release AS jsonb))"""),
            {
                "id": release.release_id,
                "index": candidate.manifest.index_id,
                "env": environment,
                "report": report.report_id,
                "release": release.model_dump_json(),
            },
        )
        connection.execute(
            text("""INSERT INTO ai.rag_qualifications(index_id,environment,lane,review_id,validation_id,validation,release_id)
            VALUES (:id,:env,'retrieval',:review,:validation,CAST(:payload AS jsonb),:release)"""),
            {
                "id": candidate.manifest.index_id,
                "env": environment,
                "review": approval.review_id,
                "validation": report.validation.validation_id,
                "payload": report.validation.model_dump_json(),
                "release": release.release_id,
            },
        )
    return True
