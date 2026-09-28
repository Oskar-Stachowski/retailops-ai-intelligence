"""Private offline release preflight, separate from DB qualification and activation."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from retailops_ai.cli_knowledge import DEFAULT_CONFIG
from retailops_ai.knowledge.golden import GoldenReport
from retailops_ai.knowledge.qualification import GoldenLabelsApproval, SimilarityReview
from retailops_ai.pipelines.corpus import write_candidate
from retailops_ai.pipelines.golden import load_golden_set
from retailops_ai.pipelines.index_builds import prepare_build_profile
from retailops_ai.pipelines.indexes import load_index_candidate
from retailops_ai.pipelines.qualification import prepare_release
from retailops_ai.pipelines.releases import load_approval, load_release_document
from retailops_ai.pipelines.retrieval import load_retrieval_config
from retailops_ai.pipelines.review import load_similarity_policy


def add_release_check(commands: Any) -> None:
    command = commands.add_parser(
        "index-release-check", help="Reproduce fake evidence and write a blocked release manifest."
    )
    for flag in ("candidate", "golden-set", "golden-report", "similarity-policy", "output"):
        command.add_argument("--" + flag, type=Path, required=True)
    command.add_argument("--retrieval-config", type=Path, default=DEFAULT_CONFIG)
    for flag in ("corpus-approval", "golden-approval", "similarity-review"):
        command.add_argument("--" + flag, type=Path)
    profile = commands.add_parser(
        "knowledge-profile-prepare", help="Prepare an approved fake/golden snapshot offline."
    )
    for flag in (
        "candidate",
        "golden-set",
        "corpus-approval",
        "golden-approval",
        "similarity-policy",
        "similarity-review",
        "output",
    ):
        profile.add_argument("--" + flag, type=Path, required=True)
    profile.add_argument("--retrieval-config", type=Path, default=DEFAULT_CONFIG)


def run_profile_prepare(args: argparse.Namespace) -> int:
    try:
        profile = prepare_build_profile(
            load_index_candidate(args.candidate),
            load_golden_set(args.golden_set),
            load_approval(args.corpus_approval),
            load_release_document(args.golden_approval, GoldenLabelsApproval),
            load_retrieval_config(args.retrieval_config),
            load_similarity_policy(args.similarity_policy),
            load_release_document(args.similarity_review, SimilarityReview),
        )
        write_candidate(profile, args.output)
    except (OSError, ValueError, RecursionError, OverflowError):
        print('{"error":"knowledge_profile_preparation_failed"}', file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "prepared",
                "profile_id": profile.profile_id,
                "purpose": profile.purpose,
                "request": profile.request().model_dump(mode="json"),
                "activation_allowed": False,
            }
        )
    )
    return 0


def run_release_check(args: argparse.Namespace) -> int:
    try:
        manifest = prepare_release(
            load_index_candidate(args.candidate),
            load_golden_set(args.golden_set),
            load_retrieval_config(args.retrieval_config),
            load_release_document(args.golden_report, GoldenReport),
            load_similarity_policy(args.similarity_policy),
            corpus_approval=load_approval(args.corpus_approval) if args.corpus_approval else None,
            golden_approval=(
                load_release_document(args.golden_approval, GoldenLabelsApproval)
                if args.golden_approval
                else None
            ),
            similarity_review=(
                load_release_document(args.similarity_review, SimilarityReview)
                if args.similarity_review
                else None
            ),
        )
        write_candidate(manifest, args.output)
    except (OSError, ValueError, RecursionError, OverflowError):
        print('{"error":"index_release_check_failed"}', file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": manifest.status,
                "release_id": manifest.release_id,
                "index_id": manifest.index_manifest.index_id,
                "blockers": manifest.blockers,
                "activation_allowed": False,
            }
        )
    )
    return 0
