"""Reserve once, run all real source preparation phases, verify, then complete."""

import math
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignSourceRecipe,
)
from retailops_ai.evaluation_campaign.campaign_export import _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation_contract import CampaignGenerationPlan
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor, scratch_bytes
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_selection_evidence import (
    verify_completed_campaign_selection,
)
from retailops_ai.evaluation_campaign.development_variants_contract import (
    ResolvedDevelopmentPreparationJournal,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    regular_file,
)

PHASES = ("generation", "qualification", "export", "import", "curation", "verify")


def _producer_pin(root: Path, commit: str) -> None:
    checked_directory(root)
    executable = shutil.which("git")
    if executable is None:
        raise SnapshotError("campaign_generation_git_unavailable")
    for arguments, expected in (
        (("rev-parse", "HEAD"), commit),
        (("diff", "HEAD", "--name-only"), ""),
        (("ls-files", "--others", "--exclude-standard", "--", "data"), ""),
    ):
        value = subprocess.check_output(  # noqa: S603 -- fixed Git read operations, no shell
            [executable, "-C", str(root), *arguments], text=True, timeout=30
        ).strip()
        if value != expected:
            raise SnapshotError("campaign_generation_dirty_or_wrong_producer")


def _source(
    ledger: CampaignJournal, operation_id: str, plan: CampaignGenerationPlan
) -> CampaignSourceRecipe:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or operation.action != "source_generate":
        raise SnapshotError("campaign_generation_requires_source_generate_operation")
    if (
        operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
    ):
        raise SnapshotError("campaign_generation_frozen_plan_mismatch")
    source = next(
        s for s in ledger.protocol.sources if s.content_sha256() == plan.source_recipe_sha256
    )
    plan.bind(source)
    return source


def _environment(root: Path) -> dict[str, str]:
    # Credentials and inherited import overrides are not passed to workers.
    env = {k: os.environ[k] for k in ("PATH", "LANG", "SYSTEMROOT") if k in os.environ}
    env.update(
        {
            k: "1"
            for k in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        }
    )
    env.update({"TMPDIR": str(root / "tmp"), "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def generate_campaign_parent(
    producer: Path,
    producer_python: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignGenerationPlan,
    selection_bundles: dict[str, Path] | None = None,
    remaining_wall_seconds: float | None = None,
) -> tuple[Path, Path, CampaignGeneratedParentReceipt]:
    ledger = campaign_journal.inspect(journal)
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or operation.action != "source_generate":
        raise SnapshotError("campaign_generation_requires_source_generate_operation")
    started = perf_counter()
    # Reserve checks ordering; completed development evidence is also required
    # before final producer inspection or any source worker can start.
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignGenerationPlan.model_validate_json(
            canonical_bytes(plan.model_dump(mode="json"))
        )
        source = _source(ledger, operation_id, plan)
        if isinstance(ledger, ResolvedDevelopmentPreparationJournal):
            # The shared entry point enforces this too: calling it directly
            # cannot bypass original costs, durable reuse or the total limit.
            from retailops_ai.evaluation_campaign.development_variants import (
                resolved_generation_allowance,
            )

            allowance = resolved_generation_allowance(journal)
            remaining_wall_seconds = (
                allowance
                if remaining_wall_seconds is None
                else min(remaining_wall_seconds, allowance)
            )
        # An enclosing, separately frozen preparation journal may only reduce
        # this attempt's original limit after accounting for previous phases.
        if remaining_wall_seconds is not None and (
            not math.isfinite(remaining_wall_seconds) or remaining_wall_seconds <= 0
        ):
            raise SnapshotError("campaign_generation_aggregate_wall_budget_exhausted")
        deadline = started + min(
            plan.resources.wall_seconds,
            remaining_wall_seconds
            if remaining_wall_seconds is not None
            else plan.resources.wall_seconds,
        )
        if operation.phase == "final":
            verify_completed_campaign_selection(journal, selection_bundles or {})
        _producer_pin(producer, source.producer_commit)
        checked_directory(output_root)
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        request = {
            "plan": plan.model_dump(mode="json"),
            "source": source.model_dump(mode="json"),
            "runtime": ledger.protocol.runtime.model_dump(mode="json"),
        }
        write(root / "request.json", request)
        worker = Path(__file__).with_name("campaign_generation_worker.py")
        snapshot_root = producer / "data/generated/ai09-campaign" / root.name / "snapshot"
        phase_results: list[dict[str, Any]] = []
        peak: int | None = None
        for phase in PHASES:
            interpreter = str(producer_python) if phase in PHASES[:3] else sys.executable
            result = monitor(
                [interpreter, "-I", "-B", str(worker), phase, str(producer), str(root)],
                root=root,
                log=root / (phase + ".log"),
                env=_environment(root),
                scratch=(root, snapshot_root),
                resources=plan.resources,
                deadline=deadline,
            )
            observed = result["sampled_tree_peak_rss_bytes"]
            if observed is not None:
                peak = max(peak or 0, int(observed))
            phase_results.append({"phase": phase, **result})
            write(root / (phase + "-resources.json"), phase_results[-1])
            handle.cost = CampaignCost(
                wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
            )
            if result["status"] != "passed":
                raise SnapshotError("campaign_generation_phase_failed_" + phase)
            output = read(root / (phase + ".json"))
            if output["worker_peak_rss_bytes"] > plan.resources.tree_rss_bytes:
                raise SnapshotError("campaign_generation_worker_peak_rss_limit")
        verified = read(root / "verify.json")
        receipt = CampaignGeneratedParentReceipt(
            protocol_sha256=ledger.protocol_sha256,
            source_recipe_sha256=source.content_sha256(),
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            source=verified["source"],
            runtime=ledger.protocol.runtime,
        )
        snapshot = Path(read(root / "import.json")["destination"]) / "snapshot"
        curated = Path(read(root / "curation.json")["destination"])
        if (
            receipt.source.schema_version != plan.snapshot_schema_version
            or any(
                getattr(receipt.source, k) != getattr(plan.parent_budget, k)
                for k in type(plan.parent_budget).model_fields
            )
            or file_hash(snapshot, "snapshot_manifest.json")[1]
            != receipt.source.snapshot_manifest_sha256
            or file_hash(curated, "curated_manifest.json")[1]
            != receipt.source.curated_manifest_sha256
            or receipt.source.source_parameters != plan.resolved_parameters
        ):
            raise SnapshotError("campaign_generation_verified_parent_changed")
        _producer_pin(producer, source.producer_commit)
        artifact_bytes = scratch_bytes((root, snapshot_root))
        if perf_counter() > deadline or artifact_bytes > plan.resources.scratch_bytes:
            raise SnapshotError("campaign_generation_completion_resource_limit")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=artifact_bytes,
        )
    validate_completed_generation(journal, receipt)
    return snapshot, curated, receipt


def validate_completed_generation(journal: Path, receipt: CampaignGeneratedParentReceipt) -> None:
    """Validate durable local evidence; this grants no additional parent access."""
    receipt = CampaignGeneratedParentReceipt.model_validate_json(
        canonical_bytes(receipt.model_dump(mode="json"))
    )
    ledger = campaign_journal.inspect(journal)
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == receipt.operation_id), None
    )
    completion = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == receipt.reservation_id
        ),
        None,
    )
    if (
        ledger.protocol_sha256 != receipt.protocol_sha256
        or receipt.runtime != ledger.protocol.runtime
        or operation is None
        or operation.action != "source_generate"
        or operation.source_recipe_sha256 != receipt.source_recipe_sha256
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
    ):
        raise SnapshotError("campaign_generation_receipt_not_completed")
    with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("campaign_generation_private_receipt_required")
        raw = stream.read(512 * 1024 + 1)
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_generation_stored_receipt_mismatch")
