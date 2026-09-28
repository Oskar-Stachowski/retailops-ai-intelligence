"""Explicit paid preparation; preserved labels and cached vectors feed offline workers."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retailops_ai.adapters.embedding_cache import CachedEmbeddingProvider
from retailops_ai.adapters.embedding_snapshot import SnapshotEmbeddingProvider, embedding_record
from retailops_ai.cli_index_jobs import load_profile
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.contracts import CorpusRegistry
from retailops_ai.knowledge.golden import GoldenSet
from retailops_ai.knowledge.jobs import GoldenProfileBase
from retailops_ai.knowledge.qualification import GoldenLabelsApproval
from retailops_ai.pipelines.chunks import build_chunks
from retailops_ai.pipelines.corpus import write_candidate
from retailops_ai.pipelines.golden import case_principal, evaluate
from retailops_ai.pipelines.index_builds import check_build_profile, prepare_build_profile
from retailops_ai.pipelines.indexes import build_index, load_embedding_config
from retailops_ai.pipelines.retrieval import KnowledgeDenied, load_retrieval_config, resolve_scope


def add_bedrock_command(commands: Any) -> None:
    command = commands.add_parser(
        "knowledge-bedrock-prepare",
        help="Explicit bounded Bedrock preparation from approved Git sources; --offline requires a complete cache.",
    )
    for flag in (
        "source-profile",
        "embedding-config",
        "retrieval-config",
        "cache-dir",
        "output-dir",
        "retailops-repo",
        "ai-repo",
    ):
        command.add_argument("--" + flag, type=Path, required=True)
    command.add_argument("--max-requests", type=int, default=550)
    command.add_argument("--max-input-bytes", type=int, default=3_000_000)
    command.add_argument("--aws-profile")
    command.add_argument("--offline", action="store_true")


def prepare(args: argparse.Namespace) -> dict[str, object]:
    from retailops_ai.adapters.bedrock_embeddings import BedrockEmbeddingProvider

    source = load_profile(args.source_profile)
    if not isinstance(source, GoldenProfileBase):
        raise ValueError("approved_golden_profile_required")
    check_build_profile(source)
    corpus = source.chunks.corpus
    registry = CorpusRegistry(
        schema_version="1.0",
        policy_version=corpus.policy_version,
        environment=corpus.environment,
        review_state="proposed",
        review_owner=corpus.review_owner,
        sources=corpus.sources,
    )
    chunks = build_chunks(
        registry,
        source.chunks.chunker,
        {
            "Oskar-Stachowski/retailops-cloud-native-platform": args.retailops_repo,
            "Oskar-Stachowski/retailops-ai-intelligence": args.ai_repo,
        },
    )
    if chunks != source.chunks:
        raise ValueError("semantic_preparation_source_mismatch")
    config = load_embedding_config(args.embedding_config)
    if config.provider != "bedrock":
        raise ValueError("bedrock_configuration_required")
    retrieval = load_retrieval_config(args.retrieval_config)
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    upstream = (
        None
        if args.offline
        else BedrockEmbeddingProvider(
            config,
            max_requests=args.max_requests,
            max_input_bytes=args.max_input_bytes,
            profile=args.aws_profile,
        )
    )
    provider = CachedEmbeddingProvider(config, args.cache_dir, upstream)
    candidate = build_index(chunks, config, provider=provider)
    # Only artifact bindings change; all cases and thresholds are preserved verbatim.
    value = source.golden_set.model_dump(mode="json", exclude={"golden_set_id"})
    value.update(index_id=candidate.manifest.index_id, retrieval_config_id=retrieval.config_id())
    value["golden_set_id"] = "golden-set-sha256-" + canonical_sha256(value)
    golden = GoldenSet.model_validate_json(json.dumps(value))
    labels_value = source.golden_approval.model_dump(mode="json", exclude={"approval_id"})
    labels_value.update(
        golden_set_id=golden.golden_set_id,
        index_id=golden.index_id,
        retrieval_config_id=golden.retrieval_config_id,
        reviewer="approved-embedding-rebind-pipeline",
        reviewer_kind="approved_pipeline",
    )
    # Retain the approval time of unchanged editorial decisions for deterministic replay.
    labels_value["approval_id"] = "golden-approval-sha256-" + canonical_sha256(labels_value)
    labels = GoldenLabelsApproval.model_validate_json(json.dumps(labels_value))
    queries = {}
    for case in golden.cases:
        try:
            resolve_scope(case_principal(case), case.request, candidate.manifest.environment)
        except KnowledgeDenied:
            continue
        record = embedding_record(
            config, case.request.question, provider.embed(case.request.question)
        )
        queries[record.embedding_id] = record
    records = tuple(queries[key] for key in sorted(queries))
    profile = prepare_build_profile(
        candidate,
        golden,
        source.approval,
        labels,
        retrieval,
        source.similarity_policy,
        source.similarity_review,
        query_embeddings=records,
    )
    report = evaluate(
        candidate, golden, retrieval, provider=SnapshotEmbeddingProvider(config, records)
    )
    for name, artifact in (
        ("candidate", candidate),
        ("golden", golden),
        ("golden-approval", labels),
        ("profile", profile),
        ("golden-report", report),
    ):
        write_candidate(artifact, args.output_dir / (name + ".json"))
    receipt: dict[str, object] = {
        "schema_version": "1.0",
        "prepared_at": datetime.now(UTC).isoformat(),
        "source_profile_id": source.profile_id,
        "source_golden_set_id": source.golden_set.golden_set_id,
        "source_approval_id": source.golden_approval.approval_id,
        "profile_id": profile.profile_id,
        "index_id": candidate.manifest.index_id,
        "golden_set_id": golden.golden_set_id,
        "labels_and_thresholds_unchanged": True,
        "requests_this_invocation": upstream.requests if upstream else 0,
        "input_tokens_this_invocation": upstream.input_tokens if upstream else 0,
        "input_bytes_this_invocation": upstream.input_bytes if upstream else 0,
        "offline_evaluation_passed": report.measured_thresholds_passed,
        "activation_allowed": False,
    }
    fd = os.open(args.output_dir / "preparation.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(receipt, output, indent=2)
        output.write("\n")
    return receipt


def run_bedrock_prepare(args: argparse.Namespace) -> int:
    from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

    try:
        receipt = prepare(args)
    except (OSError, ValueError, RecursionError, OverflowError, BotoCoreError, ClientError):
        print('{"error":"bedrock_preparation_failed"}', file=sys.stderr)
        return 2
    print(json.dumps(receipt))
    return 0
