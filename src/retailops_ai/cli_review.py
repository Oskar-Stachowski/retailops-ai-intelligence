"""Trusted offline corpus review, outside HTTP, AWS, DB and service settings."""

import argparse
import json
import sys

from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.pipelines.chunks import build_chunks, load_chunker_config
from retailops_ai.pipelines.corpus import load_registry, write_candidate
from retailops_ai.pipelines.review import load_similarity_policy, review_similarity


def run_review(args: argparse.Namespace) -> int:
    try:
        manifest = build_chunks(
            load_registry(args.registry),
            load_chunker_config(args.chunker_config),
            {
                "Oskar-Stachowski/retailops-cloud-native-platform": args.retailops_repo,
                "Oskar-Stachowski/retailops-ai-intelligence": args.ai_repo,
            },
        )
        report = review_similarity(manifest, load_similarity_policy(args.similarity_policy))
        write_candidate(report, args.output)
    except CorpusError as exc:
        print(json.dumps({"error": "corpus_review_failed", "code": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError, OverflowError):
        print('{"error":"corpus_review_failed","code":"invalid_review_input"}', file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "reviewed",
                "outcome": report.outcome,
                "report_id": report.report_id,
                "documents": len(report.documents.members),
                "chunks": len(report.chunks.members),
                "document_exact_groups": sum(
                    g.utf8_bytes > 0 and len(g.member_ids) > 1
                    for g in report.documents.content_groups
                ),
                "document_near_pairs": len(report.documents.near_pairs),
                "chunk_exact_groups": sum(
                    g.utf8_bytes > 0 and len(g.member_ids) > 1 for g in report.chunks.content_groups
                ),
                "chunk_near_pairs": len(report.chunks.near_pairs),
                "activation_allowed": False,
            }
        )
    )
    return 0
