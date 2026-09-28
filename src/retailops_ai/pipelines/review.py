"""Complete bounded lexical reports from validated candidate chunks, without merging."""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.chunks import ChunkManifest
from retailops_ai.knowledge.contracts import ManifestDocument
from retailops_ai.knowledge.review import (
    ReviewContentGroup,
    ReviewMember,
    SimilarityAnalysis,
    SimilarityPair,
    SimilarityPolicy,
    SimilarityReport,
    metadata_differences,
)
from retailops_ai.pipelines.corpus import decode_json

Shingle = tuple[str, ...]


def load_similarity_policy(path: Path) -> SimilarityPolicy:
    with path.open("rb") as source:
        raw = source.read(64001)
    if len(raw) > 64000:
        raise CorpusError("similarity_policy_too_large")
    decode_json(raw)
    return SimilarityPolicy.model_validate_json(raw)


def _features(text: str, policy: SimilarityPolicy) -> tuple[int, frozenset[Shingle]]:
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())
    if len(words) < policy.minimum_words:
        return len(words), frozenset()
    return len(words), frozenset(
        tuple(words[i : i + policy.shingle_words])
        for i in range(len(words) - policy.shingle_words + 1)
    )


def _analyze(
    level: str,
    units: list[tuple[ReviewMember, str]],
    policy: SimilarityPolicy,
    feature_budget: int,
) -> SimilarityAnalysis:
    grouped: dict[str, list[ReviewMember]] = defaultdict(list)
    features: dict[str, frozenset[Shingle]] = {}
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}
    feature_count = 0
    for member, text in sorted(units, key=lambda unit: unit[0].unit_id):
        checksum = hashlib.sha256(text.encode()).hexdigest()
        if checksum not in grouped:
            words, shingles = _features(text, policy)
            feature_count += len(shingles)
            if feature_count > feature_budget:
                raise CorpusError("similarity_feature_budget_exceeded")
            features[checksum], counts[checksum] = shingles, words
            sizes[checksum] = len(text.encode())
        grouped[checksum].append(member)
    groups = tuple(
        ReviewContentGroup(
            text_checksum=checksum,
            member_ids=tuple(m.unit_id for m in grouped[checksum]),
            utf8_bytes=sizes[checksum],
            word_count=counts[checksum],
            shingle_count=len(features[checksum]),
            eligible_for_near=counts[checksum] >= policy.minimum_words,
            metadata_differences=metadata_differences(tuple(grouped[checksum])),
        )
        for checksum in sorted(grouped)
    )
    postings: dict[Shingle, list[str]] = defaultdict(list)
    pairs: list[SimilarityPair] = []
    checked = visits = 0
    for right in sorted(features):
        right_features = features[right]
        candidates: set[str] = set()
        for shingle in sorted(right_features):
            for left in postings[shingle]:
                visits += 1
                if visits > policy.max_posting_visits:
                    raise CorpusError("similarity_posting_budget_exceeded")
                if left not in candidates:
                    checked += 1
                    if checked > policy.max_candidate_pairs:
                        raise CorpusError("similarity_pair_budget_exceeded")
                    candidates.add(left)
            postings[shingle].append(right)
        for left in sorted(candidates):
            left_features = features[left]
            if min(len(left_features), len(right_features)) * 10000 < (
                policy.threshold_bps * max(len(left_features), len(right_features))
            ):
                continue
            common = len(left_features & right_features)
            union = len(left_features) + len(right_features) - common
            if common * 10000 < policy.threshold_bps * union:
                continue
            if len(pairs) >= policy.max_report_pairs:
                raise CorpusError("similarity_report_budget_exceeded")
            pairs.append(
                SimilarityPair(
                    left_checksum=left,
                    right_checksum=right,
                    common_shingles=common,
                    union_shingles=union,
                    similarity_bps=common * 10000 // union,
                    metadata_differences=metadata_differences(
                        tuple((*grouped[left], *grouped[right]))
                    ),
                )
            )
    return SimilarityAnalysis.model_validate_json(
        json.dumps(
            {
                "level": level,
                "members": [
                    m.model_dump(mode="json") for m, _ in sorted(units, key=lambda u: u[0].unit_id)
                ],
                "content_groups": [g.model_dump(mode="json") for g in groups],
                "near_pairs": [
                    p.model_dump(mode="json")
                    for p in sorted(pairs, key=lambda p: (p.left_checksum, p.right_checksum))
                ],
                "candidate_pairs_checked": checked,
                "posting_visits": visits,
            }
        )
    )


def review_similarity(manifest: ChunkManifest, policy: SimilarityPolicy) -> SimilarityReport:
    if unicodedata.unidata_version != policy.unicode_version:
        raise CorpusError("similarity_unicode_version_mismatch")
    chunk_units: list[tuple[ReviewMember, str]] = []
    bodies: dict[str, list[str]] = defaultdict(list)
    for chunk in manifest.chunks:
        source = ManifestDocument.model_validate(
            chunk.model_dump(include=set(ManifestDocument.model_fields))
        )
        chunk_units.append(
            (
                ReviewMember(
                    unit_id=chunk.chunk_id,
                    document=source,
                    block_type=chunk.block_type,
                    heading_path=chunk.heading_path,
                    source_refs=tuple(o.source_ref for o in chunk.occurrences),
                ),
                chunk.text,
            )
        )
        bodies[chunk.document_id].append(chunk.text)
    document_units = [
        (
            ReviewMember(
                unit_id=d.document_id,
                document=d,
                block_type=None,
                heading_path=(),
                source_refs=(d.source_ref,),
            ),
            "\n".join(bodies[d.document_id]),
        )
        for d in manifest.corpus.documents
    ]
    documents = _analyze("document", document_units, policy, policy.max_features)
    remaining = policy.max_features - sum(g.shingle_count for g in documents.content_groups)
    chunks = _analyze("chunk", chunk_units, policy, remaining)
    repeated = sum(len(c.occurrences) - 1 for c in manifest.chunks)
    required = repeated > 0 or any(
        a.near_pairs or any(g.utf8_bytes > 0 and len(g.member_ids) > 1 for g in a.content_groups)
        for a in (documents, chunks)
    )
    payload = {
        "schema_version": "1.0",
        "purpose": "lexical_candidates_for_editorial_review_only",
        "corpus_id": manifest.corpus_id,
        "corpus_config_id": manifest.corpus.corpus_config_id,
        "chunk_manifest_id": manifest.chunk_manifest_id,
        "chunker_config_id": manifest.chunker_config_id,
        "environment": manifest.corpus.environment,
        "review_owner": manifest.corpus.review_owner,
        "content_trust": "untrusted_reference",
        "policy": policy.model_dump(mode="json"),
        "policy_id": policy.config_id(),
        "documents": documents.model_dump(mode="json"),
        "chunks": chunks.model_dump(mode="json"),
        "repeated_chunk_occurrences": repeated,
        "outcome": "review_required" if required else "no_lexical_candidates",
        "activation_allowed": False,
    }
    payload["report_id"] = "similarity-report-sha256-" + canonical_sha256(payload)
    return SimilarityReport.model_validate_json(json.dumps(payload))
