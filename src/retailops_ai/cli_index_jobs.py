"""Controlled profile registration and one-run execution; no auto-approval or promotion."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from retailops_ai.adapters.index_jobs import (
    cancel_run,
    execute_run,
    read_run_report,
    register_profile,
)
from retailops_ai.adapters.vector_store import index_engine
from retailops_ai.config import Settings
from retailops_ai.data_contracts.registry import _invalid_constant, _pairs
from retailops_ai.knowledge.contracts import CorpusRegistry
from retailops_ai.knowledge.indexes import MAX_INDEX_BYTES
from retailops_ai.knowledge.jobs import BUILD_PROFILE_ADAPTER, BuildProfile
from retailops_ai.pipelines.chunks import build_chunks
from retailops_ai.pipelines.corpus import write_candidate


def add_job_commands(commands: Any) -> None:
    profile = commands.add_parser(
        "knowledge-profile-register",
        help="Register an explicitly approved build snapshot after checking pinned Git sources.",
    )
    profile.add_argument("--profile", type=Path, required=True)
    profile.add_argument("--retailops-repo", type=Path, required=True)
    profile.add_argument("--ai-repo", type=Path, required=True)
    profile.add_argument("--env-file", type=Path)
    for name in ("work", "cancel"):
        command = commands.add_parser(
            "knowledge-index-" + name,
            help="Execute or cancel one persisted administrative run; never activate an index.",
        )
        command.add_argument("--run-id", required=True)
        command.add_argument("--env-file", type=Path)
    report = commands.add_parser(
        "knowledge-index-report", help="Export a private persisted run report."
    )
    report.add_argument("--run-id", required=True)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--env-file", type=Path)


def load_profile(path: Path) -> BuildProfile:
    with path.open("rb") as source:
        raw = source.read(MAX_INDEX_BYTES + 1)
    if len(raw) > MAX_INDEX_BYTES:
        raise ValueError("build_profile_size_limit")
    json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    return BUILD_PROFILE_ADAPTER.validate_json(raw)


def run_job_command(args: argparse.Namespace, settings: Settings) -> int:
    try:
        profile = None
        if args.command == "knowledge-profile-register":
            profile = load_profile(args.profile)
            if profile.environment != settings.app_env:
                raise ValueError("build_profile_environment_mismatch")
            corpus = profile.chunks.corpus
            registry = CorpusRegistry(
                schema_version="1.0",
                policy_version=corpus.policy_version,
                environment=corpus.environment,
                review_state="proposed",
                review_owner=corpus.review_owner,
                sources=corpus.sources,
            )
            rebuilt = build_chunks(
                registry,
                profile.chunks.chunker,
                {
                    "Oskar-Stachowski/retailops-cloud-native-platform": args.retailops_repo,
                    "Oskar-Stachowski/retailops-ai-intelligence": args.ai_repo,
                },
            )
            if rebuilt != profile.chunks:
                raise ValueError("build_profile_source_snapshot_mismatch")
        engine = index_engine(settings)
        try:
            if profile is not None:
                created = register_profile(engine, profile)
                result: dict[str, object] = {
                    "status": "registered" if created else "already_registered",
                    "profile_id": profile.profile_id,
                    "request": profile.request().model_dump(mode="json"),
                    "purpose": profile.purpose,
                }
            elif args.command == "knowledge-index-report":
                report = read_run_report(engine, settings.app_env, args.run_id)
                write_candidate(report, args.output)
                result = {
                    "status": "exported",
                    "report_id": report.report_id,
                    "activation_allowed": False,
                }
            else:
                run = (
                    execute_run(engine, settings.app_env, args.run_id)
                    if args.command == "knowledge-index-work"
                    else cancel_run(engine, settings.app_env, args.run_id)
                )
                result = {
                    "run_id": run.run_id,
                    "status": run.status,
                    "output_ref": run.output_ref.model_dump(mode="json")
                    if run.output_ref
                    else None,
                    "error": run.error.model_dump(mode="json") if run.error else None,
                }
        finally:
            engine.dispose()
    except (SQLAlchemyError, OSError, ValueError, RecursionError, OverflowError):
        print('{"error":"knowledge_index_operation_failed"}', file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0 if result["status"] not in {"failed", "cancelled"} else 2
