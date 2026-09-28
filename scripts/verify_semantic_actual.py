"""Explicit acceptance and activation of an already prepared, approved local profile."""

import argparse
import json
import secrets
import sys
from datetime import UTC, datetime

from retailops_ai.adapters.embedding_snapshot import SnapshotEmbeddingProvider
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
from retailops_ai.knowledge.indexes import MAX_INDEX_BYTES
from retailops_ai.knowledge.jobs import GoldenIndexRunReport, SemanticIndexBuildProfile
from retailops_ai.knowledge.releases import SwitchRequest
from retailops_ai.pipelines.golden import case_principal
from retailops_ai.pipelines.retrieval import KnowledgeDenied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--activate", action="store_true", required=True)
    parser.parse_args()
    raw = sys.stdin.buffer.read(MAX_INDEX_BYTES + 1)
    if len(raw) > MAX_INDEX_BYTES:
        raise ValueError("semantic_profile_size_limit")
    profile = SemanticIndexBuildProfile.model_validate_json(raw)
    if profile.environment != "local":
        raise ValueError("local_semantic_profile_required")
    engine = index_engine(load_settings())
    try:
        register_profile(engine, profile)
        admin = PostgresIndexAdministration(engine, "local")
        queued = admin.submit(
            profile.request(), "approved-semantic-acceptance", "accept-" + profile.profile_id[-48:]
        )
        run = execute_run(engine, "local", queued.run_id)
        report = read_run_report(engine, "local", run.run_id)
        if (
            not isinstance(report, GoldenIndexRunReport)
            or run.status != "succeeded"
            or not report.quality_gate_passed
        ):
            raise ValueError("actual_semantic_quality_gate_failed")
        qualify_semantic_run(engine, "local", run.run_id)
        current = current_index(engine, "local", "retrieval")
        if current is None or current.manifest.index_id != profile.golden_set.index_id:
            request = SwitchRequest(
                schema_version="1.0",
                request_id="rag-change-" + secrets.token_hex(16),
                environment="local",
                lane="retrieval",
                operation="activate",
                target_index_id=profile.golden_set.index_id,
                expected_generation=current.generation if current else 0,
                actor="approved-semantic-acceptance",
            )
            current = switch_index(engine, request).pin
        backend = PostgresKnowledge(
            engine,
            "local",
            profile.retrieval_config,
            provider_factory=lambda config: SnapshotEmbeddingProvider(
                config, profile.query_embeddings
            ),
        )
        for case, expected in zip(profile.golden_set.cases, report.golden.cases, strict=True):
            outcome: str
            try:
                result = backend.search_pinned(current, case.request, case_principal(case))
                outcome = result.status
                actual_ids = tuple(hit.chunk.chunk_id for hit in result.items)
            except KnowledgeDenied:
                outcome = "forbidden"
                actual_ids = ()
            if outcome != expected.outcome or actual_ids != expected.retrieved_chunk_ids:
                raise ValueError("actual_sql_golden_reproduction_mismatch:" + case.case_id)
        metadata = admin.current()
        if (
            metadata is None
            or metadata.index_id != current.manifest.index_id
            or metadata.lane != "retrieval"
        ):
            raise ValueError("semantic_current_metadata_mismatch")
        print(
            json.dumps(
                {
                    "result": "passed",
                    "checked_at": datetime.now(UTC).isoformat(),
                    "profile_id": profile.profile_id,
                    "run_id": run.run_id,
                    "run_status": run.status,
                    "report_id": report.report_id,
                    "current": metadata.model_dump(mode="json"),
                    "pin": current.model_dump(mode="json"),
                    "all_sql_cases_reproduced": len(profile.golden_set.cases),
                    "golden_report": report.golden.model_dump(mode="json"),
                    "model_calls_this_acceptance": 0,
                }
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
