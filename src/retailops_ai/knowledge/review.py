"""Bounded lexical similarity evidence; candidates never authorize deletion or activation."""

import re
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.chunks import (
    BlockType,
    ChunkConfigID,
    ChunkManifestID,
    Heading,
    chunk_id,
)
from retailops_ai.knowledge.contracts import ConfigID, CorpusID, ManifestDocument

UnitID = Annotated[str, Field(pattern=r"^(document|chunk)-sha256-[0-9a-f]{64}$")]
Difference = Literal[
    "repository",
    "document_status",
    "access_class",
    "fact_scope",
    "document_type",
    "block_type",
    "heading_path",
]
DIFFERENCES: tuple[Difference, ...] = (
    "repository",
    "document_status",
    "access_class",
    "fact_scope",
    "document_type",
    "block_type",
    "heading_path",
)


class SimilarityPolicy(Contract):
    schema_version: Literal["1.0"]
    algorithm: Literal["retrievable-word-trigram-jaccard-v1"]
    normalization: Literal["nfkc-casefold-unicode-words-v1"]
    unicode_version: Literal["14.0.0"]
    shingle_words: Literal[3]
    minimum_words: Annotated[int, Field(ge=3, le=128)]
    threshold_bps: Annotated[int, Field(ge=1, le=10000)]
    max_features: Annotated[int, Field(ge=1, le=250000)]
    max_candidate_pairs: Annotated[int, Field(ge=1, le=250000)]
    max_posting_visits: Annotated[int, Field(ge=1, le=2000000)]
    max_report_pairs: Annotated[int, Field(ge=1, le=10000)]

    def config_id(self) -> str:
        return "similarity-policy-sha256-" + canonical_sha256(self.model_dump(mode="json"))


class ReviewMember(Contract):
    unit_id: UnitID
    document: ManifestDocument
    block_type: BlockType | None
    heading_path: Annotated[tuple[Heading, ...], Field(max_length=6)]
    source_refs: Annotated[tuple[str, ...], Field(min_length=1, max_length=4096)]


def metadata_differences(members: tuple[ReviewMember, ...]) -> tuple[Difference, ...]:
    differences: list[Difference] = []
    for key in DIFFERENCES:
        if key in {"block_type", "heading_path"}:
            values = {getattr(member, key) for member in members}
        else:
            values = {getattr(member.document, key) for member in members}
        if len(values) > 1:
            differences.append(key)
    return tuple(sorted(differences))


class ReviewContentGroup(Contract):
    text_checksum: Sha256
    member_ids: Annotated[tuple[UnitID, ...], Field(min_length=1, max_length=32768)]
    utf8_bytes: Annotated[int, Field(ge=0)]
    word_count: Annotated[int, Field(ge=0)]
    shingle_count: Annotated[int, Field(ge=0)]
    eligible_for_near: bool
    metadata_differences: tuple[Difference, ...]


class SimilarityPair(Contract):
    left_checksum: Sha256
    right_checksum: Sha256
    common_shingles: Annotated[int, Field(gt=0)]
    union_shingles: Annotated[int, Field(gt=0)]
    similarity_bps: Annotated[int, Field(ge=1, le=10000)]
    metadata_differences: tuple[Difference, ...]


class SimilarityAnalysis(Contract):
    level: Literal["document", "chunk"]
    members: Annotated[tuple[ReviewMember, ...], Field(max_length=32768)]
    content_groups: Annotated[tuple[ReviewContentGroup, ...], Field(max_length=32768)]
    near_pairs: Annotated[tuple[SimilarityPair, ...], Field(max_length=10000)]
    candidate_pairs_checked: Annotated[int, Field(ge=0)]
    posting_visits: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def coverage(self) -> Self:
        by_id = {member.unit_id: member for member in self.members}
        ids = [member.unit_id for member in self.members]
        if ids != sorted(set(ids)) or (self.level == "document" and not 2 <= len(ids) <= 128):
            raise ValueError("review_member_order_or_limit")
        for member in self.members:
            if not member.unit_id.startswith(self.level + "-sha256-"):
                raise ValueError("review_member_kind_mismatch")
            if self.level == "document":
                if (
                    member.unit_id != member.document.document_id
                    or member.block_type is not None
                    or member.heading_path
                    or member.source_refs != (member.document.source_ref,)
                ):
                    raise ValueError("review_document_binding_mismatch")
            else:
                if member.block_type is None:
                    raise ValueError("review_chunk_binding_mismatch")
                for ref in member.source_refs:
                    suffix = re.fullmatch(
                        re.escape(member.document.source_ref) + r"#L([1-9][0-9]*)-L([1-9][0-9]*)",
                        ref,
                    )
                    if suffix is None or not 1 <= int(suffix[1]) <= int(suffix[2]) <= 500000:
                        raise ValueError("review_chunk_binding_mismatch")
        covered: list[str] = []
        keys = [group.text_checksum for group in self.content_groups]
        if keys != sorted(set(keys)):
            raise ValueError("review_group_order_mismatch")
        for group in self.content_groups:
            if list(group.member_ids) != sorted(set(group.member_ids)):
                raise ValueError("review_group_members_invalid")
            if any(uid not in by_id for uid in group.member_ids):
                raise ValueError("review_orphan_member")
            members = tuple(by_id[uid] for uid in group.member_ids)
            if group.metadata_differences != metadata_differences(members):
                raise ValueError("review_metadata_differences_mismatch")
            covered.extend(group.member_ids)
        if sorted(covered) != ids:
            raise ValueError("review_group_coverage_mismatch")
        return self


class SimilarityReport(Contract):
    schema_version: Literal["1.0"]
    purpose: Literal["lexical_candidates_for_editorial_review_only"]
    corpus_id: CorpusID
    corpus_config_id: ConfigID
    chunk_manifest_id: ChunkManifestID
    chunker_config_id: ChunkConfigID
    environment: Literal["local", "test", "dev", "production"]
    review_owner: Symbol
    content_trust: Literal["untrusted_reference"]
    policy: SimilarityPolicy
    policy_id: Annotated[str, Field(pattern=r"^similarity-policy-sha256-[0-9a-f]{64}$")]
    documents: SimilarityAnalysis
    chunks: SimilarityAnalysis
    repeated_chunk_occurrences: Annotated[int, Field(ge=0)]
    outcome: Literal["review_required", "no_lexical_candidates"]
    activation_allowed: FalseFlag
    report_id: Annotated[str, Field(pattern=r"^similarity-report-sha256-[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def graph(self) -> Self:
        if self.policy_id != self.policy.config_id() or self.report_id != (
            "similarity-report-sha256-" + canonical_sha256(self.identity_data())
        ):
            raise ValueError("review_identity_mismatch")
        if self.documents.level != "document" or self.chunks.level != "chunk":
            raise ValueError("review_analysis_level_mismatch")
        docs = {m.unit_id: m.document for m in self.documents.members}
        for member in self.chunks.members:
            if docs.get(member.document.document_id) != member.document:
                raise ValueError("review_document_metadata_mismatch")
        chunk_members = {m.unit_id: m for m in self.chunks.members}
        for group in self.chunks.content_groups:
            for uid in group.member_ids:
                member = chunk_members[uid]
                if member.unit_id != chunk_id(
                    member.document.document_id,
                    member.heading_path,
                    str(member.block_type),
                    group.text_checksum,
                    self.chunker_config_id,
                ):
                    raise ValueError("review_chunk_identity_mismatch")
        features = 0
        for analysis in (self.documents, self.chunks):
            groups = {g.text_checksum: g for g in analysis.content_groups}
            members = {m.unit_id: m for m in analysis.members}
            features += sum(g.shingle_count for g in analysis.content_groups)
            keys = [(p.left_checksum, p.right_checksum) for p in analysis.near_pairs]
            if keys != sorted(set(keys)) or len(keys) > self.policy.max_report_pairs:
                raise ValueError("review_pair_order_or_budget")
            if (
                analysis.candidate_pairs_checked > self.policy.max_candidate_pairs
                or analysis.posting_visits > self.policy.max_posting_visits
                or len(keys) > analysis.candidate_pairs_checked
            ):
                raise ValueError("review_comparison_budget")
            for group in analysis.content_groups:
                eligible = group.word_count >= self.policy.minimum_words
                if group.eligible_for_near != eligible or (eligible != (group.shingle_count > 0)):
                    raise ValueError("review_eligibility_mismatch")
                if group.shingle_count > max(0, group.word_count - self.policy.shingle_words + 1):
                    raise ValueError("review_shingle_count_mismatch")
            for pair in analysis.near_pairs:
                left, right = groups.get(pair.left_checksum), groups.get(pair.right_checksum)
                if (
                    left is None
                    or right is None
                    or pair.left_checksum >= pair.right_checksum
                    or not left.eligible_for_near
                    or not right.eligible_for_near
                ):
                    raise ValueError("review_pair_binding_mismatch")
                if (
                    pair.common_shingles > min(left.shingle_count, right.shingle_count)
                    or pair.union_shingles
                    != left.shingle_count + right.shingle_count - pair.common_shingles
                    or pair.similarity_bps != 10000 * pair.common_shingles // pair.union_shingles
                    or 10000 * pair.common_shingles
                    < self.policy.threshold_bps * pair.union_shingles
                ):
                    raise ValueError("review_similarity_mismatch")
                joint = tuple(members[uid] for uid in (*left.member_ids, *right.member_ids))
                if pair.metadata_differences != metadata_differences(joint):
                    raise ValueError("review_pair_metadata_mismatch")
        if features > self.policy.max_features:
            raise ValueError("review_feature_budget")
        required = self.repeated_chunk_occurrences > 0 or any(
            analysis.near_pairs
            or any(g.utf8_bytes > 0 and len(g.member_ids) > 1 for g in analysis.content_groups)
            for analysis in (self.documents, self.chunks)
        )
        if (self.outcome == "review_required") != required:
            raise ValueError("review_outcome_mismatch")
        return self

    def identity_data(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"report_id"})
