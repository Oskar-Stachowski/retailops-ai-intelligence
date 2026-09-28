"""Required-CI candidate acceptance against real pgvector; no AWS or source credentials."""

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from verify_knowledge_search import retrieval_candidate, verify_retrieval
from verify_rag_lifecycle import verify_lifecycle

from retailops_ai.adapters import vector_store
from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.contracts import REPOSITORIES, CorpusRegistry, Repository
from retailops_ai.knowledge.indexes import IndexCandidate, vector_checksum
from retailops_ai.pipelines.chunks import build_chunks, load_chunker_config
from retailops_ai.pipelines.indexes import build_index, load_embedding_config

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def synthetic_candidates(
    root: Path, environment: Literal["local", "test"] = "local"
) -> list[IndexCandidate]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git_unavailable")

    def run(repo: Path, *args: str) -> str:
        return (
            subprocess.check_output(  # noqa: S603 - fixed Git fixture operations
                [git, "-C", str(repo), *args], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )

    repos: dict[Repository, Path] = {}
    sources: list[dict[str, Any]] = []
    for ordinal, name in enumerate(REPOSITORIES):
        repo = root / str(ordinal)
        repo.mkdir()
        run(repo, "init", "-q")
        run(repo, "config", "user.name", "CI fixture")
        run(repo, "config", "user.email", "fixture@example.invalid")
        run(repo, "remote", "add", "origin", f"https://github.com/{name}.git")
        (repo / "docs").mkdir()
        body = f"# Fixture {ordinal}\n\nBody {ordinal}.\n"
        (repo / "docs/guide.md").write_text(body)
        run(repo, "add", ".")
        run(repo, "commit", "-qm", "Synthetic snapshot")
        sources.append(
            {
                "repository": name,
                "commit_sha": run(repo, "rev-parse", "HEAD"),
                "allowed_roots": ["docs"],
                "documents": [
                    {
                        "path": "docs/guide.md",
                        "title": f"Fixture {ordinal}",
                        "byte_sha256": hashlib.sha256(body.encode()).hexdigest(),
                        "document_type": "guide",
                        "document_status": "specified",
                        "access_class": "public_project",
                        "fact_scope": "Synthetic pgvector acceptance only.",
                        "implementation_refs": [],
                        "verification": None,
                    }
                ],
            }
        )
        repos[name] = repo
    registry = {
        "schema_version": "1.0",
        "policy_version": "registered-markdown-v1",
        "environment": environment,
        "review_state": "proposed",
        "review_owner": "fixture-maintainer",
        "sources": sources,
    }
    chunker = load_chunker_config(ROOT / "knowledge/chunker.v1.json")
    embedding = load_embedding_config(ROOT / "knowledge/embeddings.fake.v1.json")

    def candidate() -> IndexCandidate:
        return build_index(
            build_chunks(CorpusRegistry.model_validate_json(json.dumps(registry)), chunker, repos),
            embedding,
        )

    original = candidate()
    sources[0]["documents"][0]["access_class"] = "project_internal"
    metadata = candidate()
    smaller = build_index(original.chunks, embedding.model_copy(update={"dimension": 8}))
    repo = repos[REPOSITORIES[0]]
    raw = b"# Changed fixture\n\nDifferent candidate body.\n"
    (repo / "docs/guide.md").write_bytes(raw)
    run(repo, "add", ".")
    run(repo, "commit", "-qm", "Synthetic changed body")
    sources[0]["commit_sha"] = run(repo, "rev-parse", "HEAD")
    sources[0]["documents"][0]["byte_sha256"] = hashlib.sha256(raw).hexdigest()
    changed = candidate()
    raw = b"# Empty fixture\n"
    (repo / "docs/guide.md").write_bytes(raw)
    run(repo, "add", ".")
    run(repo, "commit", "-qm", "Synthetic removed body")
    sources[0]["commit_sha"] = run(repo, "rev-parse", "HEAD")
    sources[0]["documents"][0]["byte_sha256"] = hashlib.sha256(raw).hexdigest()
    return [original, metadata, smaller, changed, candidate()]


def verify_database(candidates: list[IndexCandidate]) -> dict[str, object]:
    original, metadata, smaller, changed, reduced = candidates
    engine = vector_store.index_engine(load_settings())
    checks: list[str] = []

    def counts() -> tuple[int, ...]:
        with engine.connect() as connection:
            return tuple(
                int(connection.scalar(text(f"SELECT count(*) FROM ai.{table}")) or 0)  # noqa: S608 - fixed table allowlist below
                for table in (
                    "rag_embedding_spaces",
                    "rag_embeddings",
                    "rag_indexes",
                    "rag_index_chunks",
                )
            )

    def rejected(operation: Callable[[Connection], object]) -> None:
        try:
            with engine.begin() as connection:
                operation(connection)
        except DBAPIError as exc:
            require(
                getattr(exc.orig, "sqlstate", None) in {"23514", "23503", "22000", "22P02"},
                "unexpected_sql_failure",
            )
        else:
            raise RuntimeError("invalid_candidate_accepted")

    def insert_parent(connection: Connection, candidate: IndexCandidate) -> None:
        m = candidate.manifest
        connection.execute(
            text("""INSERT INTO ai.rag_indexes
            (index_id,environment,space_id,dimension,chunk_count,manifest,chunk_manifest)
            VALUES (:id,:env,:space,:dim,:count,CAST(:manifest AS jsonb),CAST(:chunks AS jsonb))"""),
            {
                "id": m.index_id,
                "env": m.environment,
                "space": m.space_id,
                "dim": m.embedding_config.dimension,
                "count": m.chunk_count,
                "manifest": m.model_dump_json(),
                "chunks": candidate.chunks.model_dump_json(),
            },
        )

    try:
        vector_store.store_candidate(engine, original)
        initial = counts()
        require(
            not vector_store.store_candidate(engine, original) and counts() == initial,
            "index_replay_changed_rows",
        )
        with engine.connect() as connection:
            require(
                vector_store.read_candidate(connection, original.manifest.index_id) == original,
                "native_vector_round_trip_failed",
            )
        checks.append("native_float32_checksum_round_trip_and_idempotent_replay")
        cli_path = shutil.which("retailops-ai")
        if cli_path is None:
            raise RuntimeError("candidate_cli_unavailable")
        cli = subprocess.run(  # noqa: S603 - installed CLI, fixed subcommands, payload in stdin
            [cli_path, "index-store", "--candidate", "-"],
            input=original.model_dump_json(),
            capture_output=True,
            text=True,
            check=False,
        )
        require(
            cli.returncode == 0 and json.loads(cli.stdout).get("status") == "already_present",
            "candidate_cli_replay_failed",
        )
        cli = subprocess.run(  # noqa: S603 - invalid offline payload, no credentials in argv
            [cli_path, "index-store", "--candidate", "-"],
            input='{"private-marker":NaN}',
            capture_output=True,
            text=True,
            check=False,
        )
        require(
            cli.returncode == 2 and "private-marker" not in cli.stdout + cli.stderr,
            "candidate_cli_failed_to_redact_input",
        )
        checks.append("real_candidate_cli_stdin_replay_and_safe_validation_error")
        vector_store.store_candidate(engine, metadata)
        require(counts()[1] == initial[1], "metadata_change_reembedded_content")
        vector_store.store_candidate(engine, smaller)
        checks.append("body_cache_reuse_metadata_lineage_and_separate_dimension_spaces")
        conflicting = original.model_dump(mode="json")
        for item in conflicting["embeddings"]:
            item["vector"] = [-v for v in item["vector"]]
            item["vector_checksum"] = vector_checksum(tuple(item["vector"]))
        checksums = {
            item["embedding_id"]: item["vector_checksum"] for item in conflicting["embeddings"]
        }
        for entry in conflicting["manifest"]["entries"]:
            entry["vector_checksum"] = checksums[entry["embedding_id"]]
        conflicting["manifest"]["index_id"] = "index-sha256-" + canonical_sha256(
            {k: v for k, v in conflicting["manifest"].items() if k != "index_id"}
        )
        conflict = IndexCandidate.model_validate_json(json.dumps(conflicting))
        before_conflict = counts()
        try:
            vector_store.store_candidate(engine, conflict)
        except CorpusError as exc:
            require(str(exc) == "embedding_cache_collision", "unexpected_cache_conflict")
        else:
            raise RuntimeError("cache_collision_was_overwritten")
        require(counts() == before_conflict, "cache_conflict_changed_rows")
        checks.append("conflicting_valid_vector_cache_refused_without_overwrite")
        record = original.embeddings[0]
        invalid_vectors: list[tuple[list[float | str], int, str]] = [
            (list(record.vector[:-1]), record.dimension, record.vector_checksum),
            (list(record.vector), 8, record.vector_checksum),
            ([0.0] * record.dimension, record.dimension, record.vector_checksum),
            (list(record.vector), record.dimension, "0" * 64),
            (["NaN"] + list(record.vector[1:]), record.dimension, record.vector_checksum),
        ]
        for vector, dimension, checksum in invalid_vectors:

            def invalid_vector(
                connection: Connection,
                *,
                vector: list[float | str] = vector,
                dimension: int = dimension,
                checksum: str = checksum,
            ) -> None:
                connection.execute(
                    text("""INSERT INTO ai.rag_embeddings
                    (environment,embedding_id,space_id,content_checksum,vector_checksum,dimension,embedding)
                    VALUES ('local',:id,:space,:body,:checksum,:dimension,CAST(:vector AS vector))"""),
                    {
                        "id": "embedding-sha256-" + "f" * 64,
                        "space": record.space_id,
                        "body": record.content_checksum,
                        "checksum": checksum,
                        "dimension": dimension,
                        "vector": "[" + ",".join(map(str, vector)) + "]",
                    },
                )

            rejected(invalid_vector)
        checks.append("database_rejects_wrong_dimension_nonfinite_nonunit_and_corrupt_checksum")
        for table, column, value in [
            ("rag_embedding_spaces", "space_id", original.manifest.space_id),
            ("rag_embeddings", "embedding_id", record.embedding_id),
            ("rag_indexes", "index_id", original.manifest.index_id),
            ("rag_index_chunks", "index_id", original.manifest.index_id),
        ]:
            for action in (
                f"UPDATE ai.{table} SET {column}={column} WHERE {column}=:value",  # noqa: S608 - fixed identifiers above
                f"DELETE FROM ai.{table} WHERE {column}=:value",  # noqa: S608 - fixed identifiers above
            ):

                def mutation(
                    connection: Connection, action: str = action, value: str = value
                ) -> None:
                    connection.execute(text(action), {"value": value})

                rejected(mutation)
        checks.append("database_blocks_update_and_delete_of_every_candidate_table")
        before_failure = counts()
        rejected(lambda connection: insert_parent(connection, changed))
        require(counts() == before_failure, "partial_candidate_survived_commit")

        def mixed_space(connection: Connection) -> None:
            insert_parent(connection, changed)
            entry = changed.manifest.entries[0]
            connection.execute(
                text("""INSERT INTO ai.rag_index_chunks
                (index_id,environment,space_id,dimension,chunk_id,embedding_id,ordinal,metadata)
                VALUES (:id,'local',:space,8,:chunk,:embedding,0,CAST(:metadata AS jsonb))"""),
                {
                    "id": changed.manifest.index_id,
                    "space": smaller.manifest.space_id,
                    "chunk": entry.chunk_id,
                    "embedding": smaller.manifest.entries[0].embedding_id,
                    "metadata": changed.chunks.chunks[0].model_dump_json(),
                },
            )

        rejected(mixed_space)
        require(counts() == before_failure, "mixed_space_survived_rollback")
        original_writer = vector_store._insert_chunks

        def crash(connection: Connection, candidate: IndexCandidate) -> None:
            original_writer(connection, candidate)
            raise RuntimeError("injected_candidate_failure")

        vector_store._insert_chunks = crash
        try:
            vector_store.store_candidate(engine, changed)
        except RuntimeError as exc:
            require(str(exc) == "injected_candidate_failure", "unexpected_injected_failure")
        else:
            raise RuntimeError("candidate_failure_not_injected")
        finally:
            vector_store._insert_chunks = original_writer
        require(counts() == before_failure, "failed_build_left_partial_rows")
        checks.append("incomplete_mixed_space_and_mid_transaction_failure_roll_back_all_rows")

        def concurrent_store() -> bool:
            independent = vector_store.index_engine(load_settings())
            try:
                return vector_store.store_candidate(independent, changed)
            finally:
                independent.dispose()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: concurrent_store(), range(2)))
        require(sorted(results) == [False, True], "concurrent_replay_not_idempotent")
        vector_store.store_candidate(engine, reduced)
        with engine.connect() as connection:
            require(
                vector_store.read_candidate(connection, original.manifest.index_id) == original,
                "old_candidate_changed",
            )
            require(
                vector_store.read_candidate(connection, reduced.manifest.index_id) == reduced,
                "removed_chunks_in_new_candidate",
            )
        checks.append("concurrent_build_single_candidate_and_removed_content_absent_from_new_index")
        return {
            "checks": checks,
            "index_id": original.manifest.index_id,
            "chunks": original.manifest.chunk_count,
            "embeddings": original.manifest.embedding_count,
            "result": "passed",
        }
    finally:
        engine.dispose()


def run_in_compose(command: Callable[..., str]) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="retailops-rag-ci-") as directory:
        storage_root = Path(directory) / "storage"
        storage_root.mkdir()
        lifecycle_root = Path(directory) / "lifecycle"
        lifecycle_root.mkdir()
        candidates = synthetic_candidates(storage_root)
        lifecycle_candidates = synthetic_candidates(lifecycle_root, "test")
        retrieval_root = Path(directory) / "retrieval"
        retrieval_root.mkdir()
        retrieval = retrieval_candidate(retrieval_root)
        result = command(
            "run",
            "--rm",
            "-T",
            "--entrypoint",
            "python",
            "-v",
            f"{Path(__file__).resolve().parent}:/opt/retailops-verification:ro",
            "api-migrate",
            "/opt/retailops-verification/verify_rag_index.py",
            "--database-checks",
            stdin=json.dumps(
                {
                    "storage": [c.model_dump(mode="json") for c in candidates],
                    "lifecycle": [c.model_dump(mode="json") for c in lifecycle_candidates],
                    "retrieval": retrieval.model_dump(mode="json"),
                }
            ),
        )
        decoded: dict[str, object] = json.loads(result)
        require(decoded.get("result") == "passed", "rag_database_acceptance_failed")
        return decoded


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["--database-checks"]:
            raise RuntimeError("unsupported_verification_mode")
        raw = json.loads(sys.stdin.read(4_000_001))
        candidates = [IndexCandidate.model_validate_json(json.dumps(c)) for c in raw["storage"]]
        report = verify_database(candidates)
        report["lifecycle"] = verify_lifecycle(
            [IndexCandidate.model_validate_json(json.dumps(c)) for c in raw["lifecycle"]]
        )
        report["retrieval"] = verify_retrieval(
            IndexCandidate.model_validate_json(json.dumps(raw["retrieval"]))
        )
        print(json.dumps(report))
    except Exception as exc:
        # SQL exceptions may contain DSNs, parameters or document content.
        print(
            json.dumps(
                {
                    "error": "rag_database_acceptance_failed",
                    "type": type(exc).__name__,
                    "reason": str(exc)
                    if isinstance(exc, RuntimeError) and re.fullmatch(r"[a-z_]{1,100}", str(exc))
                    else "internal_failure",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
