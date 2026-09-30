"""Conservative disk budgets for retained cohort archives and one-at-a-time replay.

These limits control execution, never forecast quality. A stopped campaign keeps
its checkpoints; making space permits resumption with the same frozen inputs.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from retailops_ai.forecasting.functional_v12_archive import MAX_TOTAL_BYTES
from retailops_ai.forecasting.functional_v12_quality import DEFAULT_INDEX_BYTES, DEFAULT_MAX_ROWS
from retailops_ai.source_snapshot.files import SnapshotError

MIB = 1024**2
GIB = 1024**3
VERSION = "forecast-functional-resource-plan-1.0.0"


def resource_plan(
    *,
    cohort_count: int,
    max_checkpoint_bytes: int,
    max_expanded_checkpoint_bytes: int,
    max_prediction_bytes_per_cohort: int,
    max_scoring_rows_per_cohort: int,
    pilot_receipt_sha256: str,
    safety_bytes: int = 2 * GIB,
) -> dict[str, Any]:
    """Budget all retained outputs, both verification passes, and transient copies.

    The runner must enforce each per-cohort cap. Replay compares one regenerated
    prediction file at a time and retains its digest receipt, not a second full
    campaign. Downloads allow both one zip and its extracted checkpoint.
    """
    values = (
        cohort_count,
        max_checkpoint_bytes,
        max_expanded_checkpoint_bytes,
        max_prediction_bytes_per_cohort,
        max_scoring_rows_per_cohort,
        safety_bytes,
    )
    if any(type(value) is not int or value <= 0 for value in values) or (
        not 1 <= cohort_count <= 256
        or max_checkpoint_bytes > 400 * MIB
        or max_expanded_checkpoint_bytes > MAX_TOTAL_BYTES
        or cohort_count * max_scoring_rows_per_cohort > DEFAULT_MAX_ROWS
        or safety_bytes < 2 * GIB
        or len(pilot_receipt_sha256) != 64
        or any(c not in "0123456789abcdef" for c in pilot_receipt_sha256)
    ):
        raise SnapshotError("cohort_resource_plan_invalid_or_unmeasured")
    limits = {
        "cohort_count": cohort_count,
        "max_checkpoint_bytes": max_checkpoint_bytes,
        "max_expanded_checkpoint_bytes": max_expanded_checkpoint_bytes,
        "max_prediction_bytes_per_cohort": max_prediction_bytes_per_cohort,
        "max_scoring_rows_per_cohort": max_scoring_rows_per_cohort,
        "max_index_bytes": DEFAULT_INDEX_BYTES,
        "max_campaign_metadata_bytes": 64 * MIB,
        "safety_bytes": safety_bytes,
    }
    retained = cohort_count * (max_checkpoint_bytes + max_prediction_bytes_per_cohort)
    temporary = (
        max_expanded_checkpoint_bytes
        + DEFAULT_INDEX_BYTES
        + 2 * max_checkpoint_bytes
        + max_prediction_bytes_per_cohort
        + limits["max_campaign_metadata_bytes"]
        + safety_bytes
    )
    return {
        "version": VERSION,
        **limits,
        "pilot_receipt_sha256": pilot_receipt_sha256,
        "retained_budget_bytes": retained,
        "transient_and_safety_budget_bytes": temporary,
        "initial_free_bytes_required": retained + temporary,
        "replay_retention": "one_temporary_prediction_file_plus_durable_digest_receipts",
        "source_regeneration": "forbidden_use_retained_checkpoint",
        "resource_limits_are_quality_thresholds": False,
    }


def validate_resource_plan(plan: dict[str, Any]) -> None:
    keys = (
        "cohort_count",
        "max_checkpoint_bytes",
        "max_expanded_checkpoint_bytes",
        "max_prediction_bytes_per_cohort",
        "max_scoring_rows_per_cohort",
        "pilot_receipt_sha256",
        "safety_bytes",
    )
    try:
        expected = resource_plan(**{key: plan[key] for key in keys})
    except (KeyError, TypeError) as exc:
        raise SnapshotError("cohort_resource_plan_incomplete") from exc
    if plan != expected:
        raise SnapshotError("cohort_resource_plan_arithmetic_or_policy_drift")


def disk_preflight(
    plan: dict[str, Any],
    *,
    archive_volume: Path,
    work_volume: Path,
    retained_checkpoint_bytes: int = 0,
    retained_prediction_bytes: int = 0,
) -> dict[str, Any]:
    """Return a reviewable receipt without allocating, deleting, or scoring data.

    Already retained bytes are credited only against their respective output
    budget; callers obtain them from verified manifests. Reclaimable/purgeable
    OS space is deliberately excluded. Distinct volumes each need their budget.
    """
    validate_resource_plan(plan)
    count = plan["cohort_count"]
    for used, maximum in (
        (retained_checkpoint_bytes, count * plan["max_checkpoint_bytes"]),
        (retained_prediction_bytes, count * plan["max_prediction_bytes_per_cohort"]),
    ):
        if type(used) is not int or not 0 <= used <= maximum:
            raise SnapshotError("cohort_resource_retained_bytes_exceed_budget")
    archives = archive_volume.resolve(strict=True)
    work = work_volume.resolve(strict=True)
    if not archives.is_dir() or not work.is_dir():
        raise SnapshotError("cohort_resource_volume_not_directory")
    archive_free = shutil.disk_usage(archives).free
    work_free = shutil.disk_usage(work).free
    same = archives.stat().st_dev == work.stat().st_dev
    # Downloaded ZIPs and importer staging must be placed on the archive volume.
    download_and_import = 2 * plan["max_checkpoint_bytes"]
    archive_required = (
        count * plan["max_checkpoint_bytes"] - retained_checkpoint_bytes + download_and_import
    )
    work_required = (
        count * plan["max_prediction_bytes_per_cohort"]
        - retained_prediction_bytes
        + plan["transient_and_safety_budget_bytes"]
        - download_and_import
    )
    if same:
        required = archive_required + work_required
        missing = max(0, required - min(archive_free, work_free))
    else:
        required = archive_required + work_required
        missing = max(0, archive_required - archive_free) + max(0, work_required - work_free)
    return {
        "version": VERSION,
        "status": "passed" if missing == 0 else "blocked_insufficient_space",
        "archive_volume": str(archives),
        "work_volume": str(work),
        "same_device": same,
        "archive_free_bytes": archive_free,
        "work_free_bytes": work_free,
        "archive_remaining_budget_bytes": archive_required,
        "work_remaining_budget_bytes": work_required,
        "remaining_free_bytes_required": required,
        "additional_free_bytes_required": missing,
        "retained_checkpoint_bytes": retained_checkpoint_bytes,
        "retained_prediction_bytes": retained_prediction_bytes,
        "quality_evaluated": False,
    }
