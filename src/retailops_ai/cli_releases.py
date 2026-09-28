"""Controlled local CLI boundary; no HTTP or agent promotion tools."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from retailops_ai.adapters.index_lifecycle import current_index, qualify_index, switch_index
from retailops_ai.adapters.vector_store import index_engine
from retailops_ai.config import Settings
from retailops_ai.knowledge.releases import IndexValidation, SwitchRequest
from retailops_ai.pipelines.corpus import write_candidate
from retailops_ai.pipelines.indexes import load_index_candidate
from retailops_ai.pipelines.releases import load_approval, load_release_document, validate_candidate


def add_commands(commands: Any) -> None:
    validation = commands.add_parser(
        "index-validate",
        help="Write mechanical fake acceptance; never approve corpus or semantic quality.",
    )
    validation.add_argument("--candidate", type=Path, required=True)
    validation.add_argument("--output", type=Path, required=True)
    qualification = commands.add_parser(
        "index-qualify",
        help="Bind explicit corpus approval and mechanical acceptance for offline tests.",
    )
    qualification.add_argument("--index-id", required=True)
    qualification.add_argument("--approval", type=Path, required=True)
    qualification.add_argument("--validation", type=Path, required=True)
    qualification.add_argument("--lane", choices=("retrieval", "offline_test"), default="retrieval")
    qualification.add_argument("--env-file", type=Path)
    semantic = commands.add_parser(
        "index-qualify-semantic",
        help="Reproduce and qualify a successful semantic run; activation remains explicit.",
    )
    semantic.add_argument("--run-id", required=True)
    semantic.add_argument("--env-file", type=Path)
    for operation in ("activate", "rollback"):
        command = commands.add_parser(
            "index-" + operation,
            help="Atomically change a qualified offline test pointer with generation check.",
        )
        command.add_argument("--index-id", required=True)
        command.add_argument("--expected-generation", type=int, required=True)
        command.add_argument("--request-id", required=True)
        command.add_argument("--actor", required=True)
        command.add_argument("--lane", choices=("retrieval", "offline_test"), default="retrieval")
        command.add_argument("--env-file", type=Path)
    current = commands.add_parser("index-current", help="Read a single immutable index pin.")
    current.add_argument("--lane", choices=("retrieval", "offline_test"), default="retrieval")
    current.add_argument("--env-file", type=Path)


def run_validation(args: argparse.Namespace) -> int:
    try:
        report = validate_candidate(load_index_candidate(args.candidate))
        write_candidate(report, args.output)
    except (OSError, ValueError, RecursionError):
        print('{"error":"index_validation_failed"}', file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "result": report.result,
                "validation_id": report.validation_id,
                "index_id": report.index_id,
                "golden_evaluation": report.golden_evaluation,
            }
        )
    )
    return 0 if report.result == "passed" else 2


def run_database_command(args: argparse.Namespace, settings: Settings) -> int:
    try:
        engine = index_engine(settings)
        try:
            if args.command == "index-qualify-semantic":
                from retailops_ai.adapters.semantic_release import qualify_semantic_run

                created = qualify_semantic_run(engine, settings.app_env, args.run_id)
                result: dict[str, object] = {
                    "status": "qualified" if created else "already_qualified",
                    "run_id": args.run_id,
                    "lane": "retrieval",
                }
            elif args.command == "index-qualify":
                created = qualify_index(
                    engine,
                    args.index_id,
                    settings.app_env,
                    args.lane,
                    load_approval(args.approval),
                    load_release_document(args.validation, IndexValidation),
                )
                result = {
                    "status": "qualified" if created else "already_qualified",
                    "index_id": args.index_id,
                    "environment": settings.app_env,
                    "lane": args.lane,
                }
            elif args.command == "index-current":
                pin = current_index(engine, settings.app_env, args.lane)
                result = {
                    "status": "active" if pin else "not_active",
                    "pin": pin.model_dump(mode="json") if pin else None,
                }
            else:
                request = SwitchRequest.model_validate(
                    {
                        "schema_version": "1.0",
                        "request_id": args.request_id,
                        "target_index_id": args.index_id,
                        "environment": settings.app_env,
                        "lane": args.lane,
                        "actor": args.actor,
                        "expected_generation": args.expected_generation,
                        "operation": args.command.removeprefix("index-"),
                    }
                )
                result = switch_index(engine, request).model_dump(mode="json")
        finally:
            engine.dispose()
    except (SQLAlchemyError, OSError, ValueError, RecursionError):
        print('{"error":"index_lifecycle_failed"}', file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0
