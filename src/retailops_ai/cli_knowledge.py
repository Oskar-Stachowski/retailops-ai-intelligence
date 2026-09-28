"""Private candidate evaluation and controlled live deny administration."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from retailops_ai.adapters.knowledge_search import deny_document
from retailops_ai.adapters.vector_store import index_engine
from retailops_ai.config import Settings
from retailops_ai.knowledge.retrieval import DocumentDenial
from retailops_ai.pipelines.corpus import write_candidate
from retailops_ai.pipelines.golden import evaluate, load_golden_set
from retailops_ai.pipelines.indexes import load_index_candidate
from retailops_ai.pipelines.releases import load_release_document
from retailops_ai.pipelines.retrieval import load_retrieval_config

DEFAULT_CONFIG = Path(__file__).resolve().parent / "knowledge/retrieval.default.json"


def add_knowledge_commands(commands: Any) -> None:
    evaluation = commands.add_parser(
        "knowledge-evaluate",
        help="Evaluate a private candidate against draft labels; fake never enables activation.",
    )
    evaluation.add_argument("--candidate", type=Path, required=True)
    evaluation.add_argument("--golden-set", type=Path, required=True)
    evaluation.add_argument("--retrieval-config", type=Path, default=DEFAULT_CONFIG)
    evaluation.add_argument("--output", type=Path, required=True)
    denial = commands.add_parser(
        "knowledge-deny",
        help="Apply an immutable document denial to every index version in an environment.",
    )
    denial.add_argument("--denial", type=Path, required=True)
    denial.add_argument("--env-file", type=Path)


def run_evaluation(args: argparse.Namespace) -> int:
    try:
        report = evaluate(
            load_index_candidate(args.candidate),
            load_golden_set(args.golden_set),
            load_retrieval_config(args.retrieval_config),
        )
        write_candidate(report, args.output)
    except (OSError, ValueError, RecursionError, OverflowError):
        print('{"error":"knowledge_evaluation_failed"}', file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "evaluated",
                "cases": len(report.cases),
                "recall_at_5": report.recall_at_5,
                "mrr": report.mrr,
                "critical_pass_rate": report.critical_pass_rate,
                "measured_thresholds_passed": report.measured_thresholds_passed,
                "activation_allowed": False,
                "semantic_quality": report.semantic_quality,
            }
        )
    )
    return 0


def run_denial(args: argparse.Namespace, settings: Settings) -> int:
    try:
        denial = load_release_document(args.denial, DocumentDenial)
        if denial.environment != settings.app_env:
            raise ValueError("denial_environment_mismatch")
        engine = index_engine(settings)
        try:
            created = deny_document(engine, denial)
        finally:
            engine.dispose()
    except (OSError, ValueError, SQLAlchemyError, RecursionError):
        print('{"error":"knowledge_denial_failed"}', file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": "denied" if created else "already_denied", "document_id": denial.document_id}
        )
    )
    return 0
