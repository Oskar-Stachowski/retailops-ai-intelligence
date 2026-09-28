"""Package identity and safe offline configuration validation."""

import argparse
import json
import sys
from importlib.metadata import version
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import SettingsError

from retailops_ai.cli_index_jobs import add_job_commands, run_job_command
from retailops_ai.cli_knowledge import add_knowledge_commands, run_denial, run_evaluation
from retailops_ai.cli_releases import add_commands, run_database_command, run_validation
from retailops_ai.config import load_settings
from retailops_ai.contracts import ApplicationInfo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retailops-ai")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="Print package identity as JSON.")
    check = commands.add_parser("config-check", help="Validate settings without network or writes.")
    check.add_argument("--env-file", type=Path, help="Explicit dotenv file; environment wins.")
    serve = commands.add_parser("serve", help="Run the local diagnostic HTTP service.")
    serve.add_argument("--env-file", type=Path, help="Explicit dotenv file; environment wins.")
    migration = commands.add_parser("migrate", help="Explicitly upgrade the isolated AI database.")
    migration.add_argument("--env-file", type=Path)
    access_init = commands.add_parser(
        "access-init",
        help="Provision explicit local grants into private files, without printing credentials.",
    )
    access_init.add_argument("--grants-file", type=Path, required=True)
    access_init.add_argument("--output-dir", type=Path, required=True)
    access_init.add_argument("--ttl-hours", type=int, default=8)
    contracts = commands.add_parser(
        "contract-check", help="Validate an offline intelligence contract."
    )
    contracts.add_argument("family")
    contracts.add_argument("path", type=Path)
    corpus = commands.add_parser(
        "corpus-check", help="Validate registered Git sources and compile a candidate corpus."
    )
    corpus.add_argument("--registry", type=Path, required=True)
    corpus.add_argument("--retailops-repo", type=Path, required=True)
    corpus.add_argument("--ai-repo", type=Path, required=True)
    corpus.add_argument("--output", type=Path, help="Write a new immutable candidate manifest.")
    chunks = commands.add_parser(
        "chunk-build", help="Compile candidate Markdown chunks from registered Git sources."
    )
    chunks.add_argument("--registry", type=Path, required=True)
    chunks.add_argument("--chunker-config", type=Path, required=True)
    chunks.add_argument("--retailops-repo", type=Path, required=True)
    chunks.add_argument("--ai-repo", type=Path, required=True)
    chunks.add_argument("--output", type=Path, help="Write a new immutable chunk manifest.")
    index = commands.add_parser(
        "index-build", help="Build an offline fake embedding candidate from pinned Git sources."
    )
    index.add_argument("--registry", type=Path, required=True)
    index.add_argument("--chunker-config", type=Path, required=True)
    index.add_argument("--embedding-config", type=Path, required=True)
    index.add_argument("--retailops-repo", type=Path, required=True)
    index.add_argument("--ai-repo", type=Path, required=True)
    index.add_argument("--output", type=Path, required=True)
    store = commands.add_parser(
        "index-store", help="Persist a candidate in the isolated AI database; never activate."
    )
    store.add_argument("--candidate", type=Path, required=True)
    store.add_argument("--env-file", type=Path)
    add_commands(commands)
    add_knowledge_commands(commands)
    add_job_commands(commands)
    args = parser.parse_args(argv)

    if args.command == "version":
        info = ApplicationInfo(version=version("retailops-ai-intelligence"))
        print(info.model_dump_json())
        return 0

    if args.command == "index-validate":
        return run_validation(args)
    if args.command == "knowledge-evaluate":
        return run_evaluation(args)

    if args.command == "access-init":
        from retailops_ai.security.provision import provision

        try:
            provision(args.grants_file, args.output_dir, args.ttl_hours)
        except (OSError, ValueError, RecursionError):
            print('{"error":"access_initialization_failed"}', file=sys.stderr)
            return 2
        print('{"status":"initialized"}')
        return 0

    if args.command == "corpus-check":
        from retailops_ai.adapters.git_documents import CorpusError
        from retailops_ai.pipelines.corpus import build_candidate, load_registry, write_candidate

        try:
            manifest = build_candidate(
                load_registry(args.registry),
                {
                    "Oskar-Stachowski/retailops-cloud-native-platform": args.retailops_repo,
                    "Oskar-Stachowski/retailops-ai-intelligence": args.ai_repo,
                },
            )
            if args.output is not None:
                write_candidate(manifest, args.output)
        except CorpusError as exc:
            print(
                json.dumps({"error": "corpus_validation_failed", "code": str(exc)}), file=sys.stderr
            )
            return 2
        except (OSError, ValueError, RecursionError, UnicodeError):
            print(
                '{"error":"corpus_validation_failed","code":"invalid_corpus_input"}',
                file=sys.stderr,
            )
            return 2
        print(
            json.dumps(
                {
                    "status": "valid",
                    "lifecycle": "candidate",
                    "corpus_id": manifest.corpus_id,
                    "corpus_config_id": manifest.corpus_config_id,
                    "documents": len(manifest.documents),
                    "excluded_markdown": len(manifest.excluded_documents),
                    "duplicate_content_groups": len(manifest.duplicate_content_groups),
                }
            )
        )
        return 0

    if args.command in {"chunk-build", "index-build"}:
        from retailops_ai.adapters.git_documents import CorpusError
        from retailops_ai.pipelines.chunks import build_chunks, load_chunker_config
        from retailops_ai.pipelines.corpus import load_registry, write_candidate

        try:
            chunk_manifest = build_chunks(
                load_registry(args.registry),
                load_chunker_config(args.chunker_config),
                {
                    "Oskar-Stachowski/retailops-cloud-native-platform": args.retailops_repo,
                    "Oskar-Stachowski/retailops-ai-intelligence": args.ai_repo,
                },
            )
            if args.command == "index-build":
                from retailops_ai.pipelines.indexes import build_index, load_embedding_config

                candidate = build_index(
                    chunk_manifest, load_embedding_config(args.embedding_config)
                )
                write_candidate(candidate, args.output)
                print(
                    json.dumps(
                        {
                            "status": "built",
                            "lifecycle": "candidate",
                            "index_id": candidate.manifest.index_id,
                            "space_id": candidate.manifest.space_id,
                            "chunks": candidate.manifest.chunk_count,
                            "embeddings": candidate.manifest.embedding_count,
                            "semantic_quality": candidate.manifest.semantic_quality,
                        }
                    )
                )
                return 0
            if args.output is not None:
                write_candidate(chunk_manifest, args.output)
        except CorpusError as exc:
            print(
                json.dumps({"error": "chunk_validation_failed", "code": str(exc)}), file=sys.stderr
            )
            return 2
        except (OSError, ValueError, RecursionError, OverflowError):
            print(
                '{"error":"chunk_validation_failed","code":"invalid_chunk_input"}', file=sys.stderr
            )
            return 2
        print(
            json.dumps(
                {
                    "status": "valid",
                    "lifecycle": "candidate",
                    "corpus_id": chunk_manifest.corpus_id,
                    "chunk_manifest_id": chunk_manifest.chunk_manifest_id,
                    "chunker_config_id": chunk_manifest.chunker_config_id,
                    "documents": len(chunk_manifest.documents),
                    "chunks": len(chunk_manifest.chunks),
                    "duplicate_occurrences": sum(
                        len(c.occurrences) - 1 for c in chunk_manifest.chunks
                    ),
                    "omitted_blocks": sum(len(d.omitted_blocks) for d in chunk_manifest.documents),
                    "documents_without_content": sum(
                        not d.chunk_ids for d in chunk_manifest.documents
                    ),
                }
            )
        )
        return 0

    if args.command == "contract-check":
        from retailops_ai.data_contracts.registry import MAX_DOCUMENT_BYTES, validate_document

        try:
            with args.path.open("rb") as source:
                raw = source.read(MAX_DOCUMENT_BYTES + 1)
            validate_document(args.family, raw)
        except (OSError, ValueError, RecursionError):
            print('{"error":"invalid_contract_document"}', file=sys.stderr)
            return 2
        print(json.dumps({"status": "valid", "contract": args.family, "schema_version": "1.0"}))
        return 0

    if args.env_file is not None and not args.env_file.is_file():
        print('{"error":"configuration_file_unavailable"}', file=sys.stderr)
        return 2
    try:
        settings = load_settings(args.env_file)
    except ValidationError as exc:
        fields = sorted(
            {
                str(error["loc"][0])
                for error in exc.errors(
                    include_url=False, include_context=False, include_input=False
                )
                if error["loc"]
            }
        )
        print(json.dumps({"error": "invalid_configuration", "fields": fields}), file=sys.stderr)
        return 2
    except (SettingsError, OSError, UnicodeError):
        print('{"error":"configuration_unavailable"}', file=sys.stderr)
        return 2

    if args.command in {"index-qualify", "index-activate", "index-rollback", "index-current"}:
        return run_database_command(args, settings)
    if args.command == "knowledge-deny":
        return run_denial(args, settings)
    if args.command in {
        "knowledge-profile-register",
        "knowledge-index-work",
        "knowledge-index-cancel",
    }:
        return run_job_command(args, settings)

    if args.command == "migrate":
        from retailops_ai.migrations.runner import migrate

        try:
            migrate(settings)
        except Exception:
            print('{"error":"database_migration_failed"}', file=sys.stderr)
            return 1
        print('{"status":"migrated"}')
        return 0

    if args.command == "index-store":
        from sqlalchemy.exc import SQLAlchemyError

        from retailops_ai.adapters.git_documents import CorpusError
        from retailops_ai.adapters.vector_store import index_engine, store_candidate
        from retailops_ai.knowledge.indexes import MAX_INDEX_BYTES
        from retailops_ai.pipelines.indexes import load_index_candidate, validate_index_bytes

        try:
            candidate = (
                validate_index_bytes(sys.stdin.buffer.read(MAX_INDEX_BYTES + 1))
                if args.candidate == Path("-")
                else load_index_candidate(args.candidate)
            )
            if candidate.manifest.environment != settings.app_env:
                raise CorpusError("index_environment_mismatch")
            engine = index_engine(settings)
            try:
                created = store_candidate(engine, candidate)
            finally:
                engine.dispose()
        except (SQLAlchemyError, OSError, ValueError, RecursionError):
            print('{"error":"index_storage_failed"}', file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "status": "stored" if created else "already_present",
                    "lifecycle": "candidate",
                    "index_id": candidate.manifest.index_id,
                    "chunks": candidate.manifest.chunk_count,
                    "embeddings": candidate.manifest.embedding_count,
                }
            )
        )
        return 0

    if args.command == "serve":
        import uvicorn

        from retailops_ai.adapters.telemetry import logging_config
        from retailops_ai.api.app import create_app

        try:
            app = create_app(settings)
        except (OSError, ValueError):
            print('{"error":"access_policy_unavailable"}', file=sys.stderr)
            return 2
        uvicorn.run(
            app,
            host=settings.http_host,
            port=settings.http_port,
            log_config=logging_config(settings.log_level),
            access_log=False,
            proxy_headers=False,
            server_header=False,
            ws="none",
            lifespan="on",
            timeout_graceful_shutdown=5,
        )
        return 0

    from retailops_ai.security.local import load_authority

    try:
        load_authority(
            settings.api_auth_file,
            settings.metrics_token.get_secret_value() if settings.metrics_token else None,
        )
    except (OSError, ValueError):
        print('{"error":"access_policy_unavailable"}', file=sys.stderr)
        return 2
    print(json.dumps({"status": "valid", "app_env": settings.app_env}))
    return 0
