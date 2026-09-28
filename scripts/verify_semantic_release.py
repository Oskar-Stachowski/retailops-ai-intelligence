"""Offline semantic lifecycle fixtures on real pgvector; these do not measure model quality."""

import json
import secrets
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from verify_golden_jobs import fixture_profile
from verify_rag_lifecycle import require

from retailops_ai.adapters.embedding_snapshot import SnapshotEmbeddingProvider, embedding_record
from retailops_ai.adapters.embeddings import FakeEmbeddingProvider
from retailops_ai.adapters.index_jobs import (
    PostgresIndexAdministration,
    execute_run,
    read_run_report,
    register_profile,
)
from retailops_ai.adapters.index_lifecycle import current_index, switch_index
from retailops_ai.adapters.knowledge_search import PostgresKnowledge
from retailops_ai.adapters.semantic_release import qualify_semantic_run
from retailops_ai.adapters.vector_store import index_engine
from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.golden import GoldenSet
from retailops_ai.knowledge.indexes import EmbeddingConfig, IndexCandidate
from retailops_ai.knowledge.jobs import GoldenIndexRunReport, SemanticIndexBuildProfile
from retailops_ai.knowledge.qualification import GoldenLabelsApproval
from retailops_ai.knowledge.releases import SwitchRequest
from retailops_ai.pipelines.golden import case_principal
from retailops_ai.pipelines.index_builds import prepare_build_profile
from retailops_ai.pipelines.indexes import build_index


def semantic_fixture(
    source: IndexCandidate, dimension: int, *, fail: bool = False
) -> SemanticIndexBuildProfile:
    fake = fixture_profile(source, fail=fail)
    values = fake.embedding_config.model_dump(mode="json")
    values.update(
        provider="bedrock",
        model_id="amazon.titan-embed-text-v2:0",
        region="eu-north-1",
        dimension=dimension,
        transformation_version="utf8-heading-path-body-v1",
    )
    config = EmbeddingConfig.model_validate_json(json.dumps(values))
    original = FakeEmbeddingProvider(fake.embedding_config)

    class FixtureProvider:
        def __init__(self) -> None:
            self.config = config

        def embed(self, value: str) -> tuple[float, ...]:
            # Fixed fixture transform, never passed off as a measured real model.
            body = value.split("\n\n", 1)[-1]
            return (
                *original.embed(body),
                *((0.0,) * (dimension - fake.embedding_config.dimension)),
            )

    provider = FixtureProvider()
    candidate = build_index(source.chunks, config, provider=provider)
    value = fake.golden_set.model_dump(mode="json", exclude={"golden_set_id"})
    value["index_id"] = candidate.manifest.index_id
    value["golden_set_id"] = "golden-set-sha256-" + canonical_sha256(value)
    golden = GoldenSet.model_validate_json(json.dumps(value))
    value = fake.golden_approval.model_dump(mode="json", exclude={"approval_id"})
    value.update(index_id=golden.index_id, golden_set_id=golden.golden_set_id)
    value["approval_id"] = "golden-approval-sha256-" + canonical_sha256(value)
    labels = GoldenLabelsApproval.model_validate_json(json.dumps(value))
    records = {
        c.request.question: embedding_record(
            config, c.request.question, provider.embed(c.request.question)
        )
        for c in golden.cases
    }
    return prepare_build_profile(
        candidate,
        golden,
        fake.approval,
        labels,
        fake.retrieval_config,
        fake.similarity_policy,
        fake.similarity_review,
        query_embeddings=tuple(sorted(records.values(), key=lambda r: r.embedding_id)),
    )


def verify_semantic_lifecycle(source: IndexCandidate) -> dict[str, Any]:
    require(source.manifest.environment == "test", "semantic_fixture_requires_test")
    engine = index_engine(load_settings())
    admin = PostgresIndexAdministration(engine, "test")
    profiles = [
        semantic_fixture(source, 256),
        semantic_fixture(source, 512),
        semantic_fixture(source, 1024, fail=True),
    ]
    runs = []
    try:
        for profile in profiles:
            register_profile(engine, profile)
            queued = admin.submit(
                profile.request(), "semantic-fixture-admin", "semantic-" + secrets.token_hex(8)
            )
            run = execute_run(engine, "test", queued.run_id)
            report = read_run_report(engine, "test", run.run_id)
            require(isinstance(report, GoldenIndexRunReport), "semantic_report_missing")
            runs.append(run)
        require(
            [r.status for r in runs] == ["succeeded", "succeeded", "failed"],
            "semantic_fixture_gate_mismatch",
        )
        try:
            qualify_semantic_run(engine, "test", runs[2].run_id)
        except ValueError:
            pass
        else:
            raise RuntimeError("semantic_failed_run_qualified")
        for run in runs[:2]:
            require(
                qualify_semantic_run(engine, "test", run.run_id), "semantic_qualification_missing"
            )
            require(
                not qualify_semantic_run(engine, "test", run.run_id),
                "semantic_qualification_not_idempotent",
            )

        def switch(
            ordinal: int, operation: str = "activate", generation: int | None = None
        ) -> SwitchRequest:
            current = current_index(engine, "test", "retrieval")
            return SwitchRequest.model_validate_json(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "request_id": "rag-change-" + secrets.token_hex(16),
                        "environment": "test",
                        "lane": "retrieval",
                        "operation": operation,
                        "target_index_id": profiles[ordinal].golden_set.index_id,
                        "expected_generation": generation
                        if generation is not None
                        else (current.generation if current else 0),
                        "actor": "semantic-fixture-admin",
                    }
                )
            )

        first_request = switch(0)
        first = switch_index(engine, first_request).pin
        require(switch_index(engine, first_request).pin == first, "semantic_switch_replay_changed")
        second = switch_index(engine, switch(1)).pin
        try:
            switch_index(engine, switch(0, generation=first.generation))
        except ValueError:
            pass
        else:
            raise RuntimeError("semantic_stale_generation_accepted")
        rollback = switch_index(engine, switch(0, "rollback")).pin
        require(rollback.generation == second.generation + 1, "semantic_rollback_generation_wrong")
        profile = profiles[0]
        backend = PostgresKnowledge(
            engine,
            "test",
            profile.retrieval_config,
            provider_factory=lambda cfg: SnapshotEmbeddingProvider(cfg, profile.query_embeddings),
        )
        case = profile.golden_set.cases[0]
        result = backend.search_pinned(first, case.request, case_principal(case))
        require(
            result.status == "ok" and result.index_id == first.manifest.index_id,
            "semantic_pinned_read_failed",
        )
        # Database constraints must also reject direct insertion without a release.
        report = read_run_report(engine, "test", runs[2].run_id)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("""INSERT INTO ai.rag_qualifications(index_id,environment,lane,review_id,validation_id,validation)
                    VALUES(:id,'test','retrieval',:review,:validation,CAST(:payload AS jsonb))"""),
                    {
                        "id": report.validation.index_id,
                        "review": profiles[2].approval.review_id,
                        "validation": report.validation.validation_id,
                        "payload": report.validation.model_dump_json(),
                    },
                )
        except DBAPIError as error:
            require(
                getattr(error.orig, "sqlstate", None) == "23514", "semantic_sql_wrong_rejection"
            )
        else:
            raise RuntimeError("semantic_sql_missing_release_accepted")
        return {
            "result": "passed",
            "fixture_only_no_aws": True,
            "checks": [
                "real_dimensions_and_context_checksum",
                "durable_success_and_retained_failed_gate",
                "offline_report_reproduction_before_qualification",
                "qualified_activate_replay_cas_rollback",
                "immutable_old_pin_search",
                "sql_missing_release_rejected",
            ],
            "retained_runs": [r.model_dump(mode="json") for r in runs],
            "final_pin": rollback.model_dump(mode="json"),
        }
    finally:
        engine.dispose()
