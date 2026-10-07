"""Conservative, pinned evidence of a lost diagnostic campaign, never a restored ledger."""

import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, SourceID
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.development_contract import DevelopmentProtocol
from retailops_ai.source_snapshot.files import SnapshotError, decode_json, read_bytes

EVIDENCE_COMMIT: Final = "7f10f8e42f110423381aa0f934bba60b78db0bb4"
PUBLISHED_PROTOCOL_DIGEST: Final = (
    "c76d4c484b8694ebb7f9957aaa9245b72955114d49792f02375cf80bd5fb32c7"
)
PUBLISHED_ATTEMPTS_DIGEST: Final = (
    "454cdec3ae3b8350eab5a4e86a4116483b1fca92a413d7b9744352eb8a9b5f72"
)
EVIDENCE_PINS = MappingProxyType(
    {
        "09-03-forecast-development-comparison.json": (
            "5693bca4fba6db5356c5da8744cacd05acdf97dab1df4d22a6cb4b47386b881d"
        ),
        "09-05-development-trial-registry.json": (
            "d6157df3233aa12bf163c67b5287807b34e04796e14b52eecf99c4326c04d6e4"
        ),
        "09-10-forecast-source-versions.json": (
            "6e948c395371e226af5cb894a62847a87560dc2e154ac47da13ba1f63d0c2c61"
        ),
    }
)


class PublishedLegacyAttempt(Contract):
    """Only fields present in the published receipt; original file seals are missing."""

    output: str
    status: Literal["completed_development_diagnostic", "interrupted"]
    protocol_sha256: Sha256
    files: Annotated[int, Field(ge=1)]
    artifact_bytes: Annotated[int, Field(ge=1)]
    model_starts: Annotated[int, Field(ge=0, le=4)]
    model_completions: Annotated[int, Field(ge=0, le=4)]
    cost_files: tuple[str, ...]

    @model_validator(mode="after")
    def counts(self) -> Self:
        if self.model_completions > self.model_starts:
            raise ValueError("legacy_completion_count_exceeds_starts")
        return self


class LegacyCampaignCarryover(Contract):
    scope: Literal["published_evidence_reconciliation_not_a_restored_journal"] = (
        "published_evidence_reconciliation_not_a_restored_journal"
    )
    version: Literal["ai09-lost-diagnostic-campaign-carryover-1.0.0"] = (
        "ai09-lost-diagnostic-campaign-carryover-1.0.0"
    )
    evidence_commit: Literal["7f10f8e42f110423381aa0f934bba60b78db0bb4"] = EVIDENCE_COMMIT
    evidence_sha256: dict[str, Sha256]
    published_protocol: DevelopmentProtocol
    published_attempts: Annotated[
        tuple[PublishedLegacyAttempt, ...], Field(min_length=11, max_length=11)
    ]
    published_fit_starts: Literal[44] = 44
    published_fit_completions: Literal[40] = 40
    published_completed_attempts: Literal[5] = 5
    published_interrupted_attempts: Literal[6] = 6
    published_access_plans: Literal[4] = 4
    last_published_reserved_project_reads: Literal[0] = 0
    last_published_remaining_project_reads: Literal[64] = 64
    unavailable_legacy_read_slots_retired: Literal[64] = 64
    unavailable_legacy_fit_slots_retired: Literal[4] = 4
    later_unrecorded_usage: Literal["unknown_not_zero"] = "unknown_not_zero"
    full_historical_cost: Literal["unknown_not_zero"] = "unknown_not_zero"
    legacy_budget_available: Literal[0] = 0
    original_journal_bytes_recovered: FalseFlag = False
    original_artifact_bytes_recovered: FalseFlag = False
    pre_read_or_pre_fit_history_restored: FalseFlag = False
    unlisted_data_freshness: Literal["unknown_not_unseen"] = "unknown_not_unseen"
    independent_evaluation_access_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def reconcile(self) -> Self:
        if self.evidence_sha256 != EVIDENCE_PINS:
            raise ValueError("legacy_evidence_pins_mismatch")
        attempts = self.published_attempts
        if (
            canonical_sha256(self.published_protocol.model_dump(mode="json"))
            != PUBLISHED_PROTOCOL_DIGEST
            or canonical_sha256([a.model_dump(mode="json") for a in attempts])
            != PUBLISHED_ATTEMPTS_DIGEST
        ):
            raise ValueError("legacy_published_metadata_digest_mismatch")
        if (
            len({a.output for a in attempts}) != 11
            or len({a.protocol_sha256 for a in attempts}) != 10
            or sum(a.model_starts for a in attempts) != self.published_fit_starts
            or sum(a.model_completions for a in attempts) != self.published_fit_completions
            or sum(a.status == "completed_development_diagnostic" for a in attempts) != 5
            or sum(a.status == "interrupted" for a in attempts) != 6
            or self.published_protocol.final_test_accessed
        ):
            raise ValueError("legacy_published_history_reconciliation_mismatch")
        return self

    def content_sha256(self) -> str:
        # Frozen Pydantic objects can contain mutable dictionaries. Revalidate
        # before this metadata can be bound into a future campaign protocol.
        document = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(document))
        return canonical_sha256(document)

    def source_freshness(
        self, source_dataset_id: SourceID
    ) -> Literal["previously_exposed", "unknown_not_unseen"]:
        """New IDs, seeds or role names do not grant unseen status or final access."""
        self.content_sha256()
        return (
            "previously_exposed"
            if source_dataset_id == self.published_protocol.parent.source_dataset_id
            else "unknown_not_unseen"
        )


def load_legacy_carryover(evidence: Path) -> LegacyCampaignCarryover:
    """Read exactly three pinned public metadata files, never their historical paths.

    This declares inaccessible diagnostic budgets unavailable. A future campaign
    must explicitly bind this history and preregister its own distinct prospective
    scope and budget. This function neither creates a journal nor permits I/O to
    outcomes, fitting, independent evaluation, final test or promotion.
    """
    documents = {}
    for name, expected in EVIDENCE_PINS.items():
        try:
            raw = read_bytes(evidence, name, 4 * 1024**2)
        except OSError as exc:
            raise SnapshotError("legacy_carryover_evidence_unavailable") from exc
        if hashlib.sha256(raw).hexdigest() != expected:
            raise SnapshotError("legacy_carryover_evidence_bytes_mismatch")
        documents[name] = decode_json(raw)
    comparison = documents["09-03-forecast-development-comparison.json"]
    registry = documents["09-05-development-trial-registry.json"]["inventory"]
    access = documents["09-10-forecast-source-versions.json"]["shared_project_journal"]
    summary = registry["summary"]
    if (
        summary["historical_attempts"] != 11
        or summary["historical_model_starts"] != 44
        or summary["historical_model_completions"] != 40
        or summary["reserved_new_attempts"] != 0
        or summary["charged_fit_slots"] != 0
        or summary["maximum_fit_slots"] != 4
        or access["historical_attempts"] != 11
        or access["historical_protocols"] != 10
        or access["registered_plans"] != 4
        or any(
            access[k] != 0
            for k in ("reserved_reads", "completed_reads", "failed_reads", "unresolved_reads")
        )
        or access["remaining_read_budget"] != 64
    ):
        raise SnapshotError("legacy_carryover_published_summary_mismatch")
    return LegacyCampaignCarryover.model_validate_json(
        canonical_bytes(
            {
                "evidence_sha256": dict(EVIDENCE_PINS),
                "published_protocol": comparison["protocol"],
                "published_attempts": registry["history"],
            }
        )
    )
