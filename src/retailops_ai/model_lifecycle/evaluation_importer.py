"""Prepare bounded development evidence from immutable AI 04 exports."""

from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.model_lifecycle.contracts import Receipt
from retailops_ai.model_lifecycle.evaluation_contracts import (
    EvaluationDescriptor,
    EvaluationEvidence,
)


def import_evidence(root: Path) -> EvaluationEvidence:
    # Heavy offline verifiers are never imported or executed by HTTP handlers.
    from retailops_ai.forecasting.quality import load_quality
    from retailops_ai.forecasting.run import verify_run
    from retailops_ai.model_lifecycle.evaluation_replay import replay
    from retailops_ai.source_snapshot.files import file_hash

    run = verify_run(root)
    quality = load_quality(root / "quality")
    scope, count, membership_sha, metrics = replay(root)

    def receipt(path: str) -> Receipt:
        size, sha = file_hash(root, path)
        return Receipt(size_bytes=size, sha256=sha)

    d = run.descriptor
    descriptor = EvaluationDescriptor(
        purpose="historical_development_evidence",
        evaluation_id=quality.quality_id,
        original_export_run_id=run.run_id,
        scope=scope,
        quality_status=quality.descriptor.quality_status,
        source_dataset_id=d.source_dataset_id,
        curated_dataset_id=d.curated_dataset_id,
        feature_set_id=d.feature_set_id,
        label_dataset_id=d.label_dataset_id,
        split_id=d.split_id,
        backtest_id=d.backtest_id,
        source_code_commit=d.source_code_commit,
        ai_code_commit=d.ai_code_commit,
        dependency_lock_sha256=d.dependency_lock_sha256,
        generated_at=quality.generated_at,
        export_manifest=receipt("run_manifest.json"),
        quality_manifest=receipt("quality/quality_manifest.json"),
        segments_report=receipt("quality/segments.json"),
        membership_content_sha256=membership_sha,
        evaluation_membership_rows=count,
        metrics=metrics,
    )
    # Recheck every source receipt after metric replay and scope extraction.
    if verify_run(root) != run:
        raise ValueError("evaluation_export_changed_during_import")
    if len(canonical_bytes(descriptor.model_dump(mode="json"))) > 64 * 1024:
        raise ValueError("evaluation_evidence_byte_limit")
    return EvaluationEvidence(
        evidence_sha256=canonical_sha256(descriptor.model_dump(mode="json")), descriptor=descriptor
    )
