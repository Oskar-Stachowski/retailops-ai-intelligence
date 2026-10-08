"""Durably charge planned operations before source I/O or fitting can begin."""

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignEvent,
    CampaignJournal,
    CampaignProtocol,
    SelectionFreeze,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    parse_campaign_journal,
    parse_campaign_protocol,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    directory_fd,
    regular_file,
)

MAX_JOURNAL_BYTES = 4 * 1024**2


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    checked_directory(root)
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise SnapshotError("campaign_private_directory_required")
    with directory_fd(root) as root_fd:
        descriptor = os.open(
            "journal.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=root_fd,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise SnapshotError("campaign_private_regular_lock_required")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _read(root: Path) -> CampaignJournal:
    with regular_file(root, "journal.json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("campaign_private_journal_required")
        raw = stream.read(MAX_JOURNAL_BYTES + 1)
    if len(raw) > MAX_JOURNAL_BYTES:
        raise SnapshotError("campaign_journal_size_limit")
    ledger = parse_campaign_journal(canonical_bytes(decode_json(raw)))
    if ledger.protocol.journal_path != str(root.absolute()):
        raise SnapshotError("campaign_journal_location_mismatch")
    if raw != canonical_bytes(ledger.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_journal_noncanonical")
    return ledger


def _publish(root: Path, ledger: CampaignJournal) -> None:
    raw = canonical_bytes(ledger.model_dump(mode="json")) + b"\n"
    if len(raw) > MAX_JOURNAL_BYTES:
        raise SnapshotError("campaign_journal_size_limit")
    temporary = ".campaign-journal-" + uuid4().hex
    with directory_fd(root) as root_fd:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_fd,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, "journal.json", src_dir_fd=root_fd, dst_dir_fd=root_fd)
            os.fsync(root_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=root_fd)
            except FileNotFoundError:
                pass


def _runtime(ledger: CampaignJournal) -> None:
    if ledger.protocol.runtime != runtime_pin():
        raise SnapshotError("campaign_execution_runtime_changed")


def initialize(root: Path, protocol: CampaignProtocol) -> CampaignJournal:
    protocol = parse_campaign_protocol(canonical_bytes(protocol.model_dump(mode="json")))
    if protocol.journal_path != str(root.absolute()):
        raise SnapshotError("campaign_journal_location_mismatch")
    digest = protocol.content_sha256()
    proposed = parse_campaign_journal(
        canonical_bytes(
            {
                "protocol": protocol.model_dump(mode="json"),
                "protocol_sha256": digest,
                "head_sha256": digest,
            }
        )
    )
    _runtime(proposed)
    checked_directory(root.parent)
    try:
        root.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        created = False
    with _locked(root):
        if (root / "journal.json").exists() or (root / "journal.json").is_symlink():
            previous = _read(root)
            if previous.protocol != protocol:
                raise SnapshotError("campaign_protocol_already_frozen")
            return previous
        if not created:
            raise SnapshotError("campaign_missing_journal_cannot_reset")
        _publish(root, proposed)
        return proposed


def inspect(root: Path) -> CampaignJournal:
    """Inspect a historical runtime without granting new execution permission."""
    with _locked(root):
        return _read(root)


def _append(root: Path, ledger: CampaignJournal, **values: Any) -> CampaignJournal:
    event = CampaignEvent(
        sequence=len(ledger.events) + 1,
        previous_sha256=ledger.head_sha256,
        at=datetime.now(UTC),
        **values,
    )
    document = ledger.model_dump(mode="json") | {
        "events": [
            *(e.model_dump(mode="json") for e in ledger.events),
            event.model_dump(mode="json"),
        ],
        "head_sha256": canonical_sha256(event.model_dump(mode="json")),
    }
    next_ledger = parse_campaign_journal(canonical_bytes(document))
    _publish(root, next_ledger)
    return next_ledger


def reserve(root: Path, operation_id: str) -> CampaignEvent:
    with _locked(root):
        ledger = _read(root)
        _runtime(ledger)
        ledger = _append(
            root,
            ledger,
            kind="reserved",
            operation_id=operation_id,
            reservation_id="campaign-operation-" + uuid4().hex,
        )
        return ledger.events[-1]


def finish(
    root: Path,
    reservation_id: str,
    *,
    result: Literal["completed", "failed"],
    evidence_sha256: str | None = None,
    cost: CampaignCost | None = None,
    error_code: str | None = None,
) -> CampaignEvent:
    """Finishing does no source I/O; failures can be recorded after a runtime change."""
    with _locked(root):
        ledger = _read(root)
        start = next(
            (
                e
                for e in ledger.events
                if e.kind == "reserved" and e.reservation_id == reservation_id
            ),
            None,
        )
        if start is None:
            raise SnapshotError("campaign_unknown_reservation")
        previous = next(
            (
                e
                for e in ledger.events
                if e.kind == "finished" and e.reservation_id == reservation_id
            ),
            None,
        )
        if previous is not None:
            if (previous.result, previous.evidence_sha256, previous.cost, previous.error_code) != (
                result,
                evidence_sha256,
                cost,
                error_code,
            ):
                raise SnapshotError("campaign_operation_result_already_frozen")
            return previous
        if result == "completed":
            _runtime(ledger)
        ledger = _append(
            root,
            ledger,
            kind="finished",
            operation_id=start.operation_id,
            reservation_id=reservation_id,
            result=result,
            evidence_sha256=evidence_sha256,
            cost=cost,
            error_code=error_code,
        )
        return ledger.events[-1]


def freeze_selection(root: Path, selection: SelectionFreeze) -> CampaignEvent:
    selection = SelectionFreeze.model_validate_json(
        canonical_bytes(selection.model_dump(mode="json"))
    )
    with _locked(root):
        ledger = _read(root)
        _runtime(ledger)
        previous = next((e for e in ledger.events if e.kind == "selection_frozen"), None)
        if previous is not None:
            if previous.selection != selection:
                raise SnapshotError("campaign_selection_already_frozen")
            return previous
        return _append(root, ledger, kind="selection_frozen", selection=selection).events[-1]


def close(root: Path, report_sha256: str) -> CampaignEvent:
    """Close audited execution; this does not mark data, quality or AI 09 ready."""
    with _locked(root):
        ledger = _read(root)
        _runtime(ledger)
        previous = next((e for e in ledger.events if e.kind == "closed"), None)
        if previous is not None:
            if previous.evidence_sha256 != report_sha256:
                raise SnapshotError("campaign_close_report_already_frozen")
            return previous
        return _append(root, ledger, kind="closed", evidence_sha256=report_sha256).events[-1]


@dataclass
class OperationCompletion:
    reservation: CampaignEvent
    evidence_sha256: str | None = None
    cost: CampaignCost | None = None


@contextmanager
def audited_operation(root: Path, operation_id: str) -> Iterator[OperationCompletion]:
    """Put generation, reading or fitting entirely inside the reservation context.

    The runner supplies a verified output receipt and measured costs before exit.
    A SIGKILL leaves a charged unresolved reservation; ordinary failure is charged
    and recorded without sensitive exception text. There is no budget refund.
    """
    started = perf_counter()
    handle = OperationCompletion(reserve(root, operation_id))
    reservation_id = str(handle.reservation.reservation_id)
    try:
        yield handle
        if handle.evidence_sha256 is None or handle.cost is None:
            raise SnapshotError("campaign_missing_completion_evidence")
        finish(
            root,
            reservation_id,
            result="completed",
            evidence_sha256=handle.evidence_sha256,
            cost=handle.cost,
        )
    except BaseException:
        # Ordinary failures have an observed wall cost. A SIGKILL still leaves
        # an unresolved charge with unknown costs; never replace it with zero.
        partial = handle.cost
        finish(
            root,
            reservation_id,
            result="failed",
            error_code="campaign_operation_failed",
            cost=CampaignCost(
                wall_seconds=perf_counter() - started,
                peak_process_tree_rss_bytes=(
                    partial.peak_process_tree_rss_bytes if partial is not None else None
                ),
                artifact_bytes=partial.artifact_bytes if partial is not None else None,
            ),
        )
        raise


def summary(root: Path) -> dict[str, int | str | bool]:
    ledger = inspect(root)
    starts = [e for e in ledger.events if e.kind == "reserved"]
    ends = [e for e in ledger.events if e.kind == "finished"]
    ended = {e.reservation_id: e for e in ends}
    plans = {p.operation_id: p for p in ledger.protocol.operations}
    fit_starts = sum(
        plans[str(e.operation_id)].action in ("model_fit", "calibrator_fit") for e in starts
    )
    return {
        "protocol_sha256": ledger.protocol_sha256,
        "journal_head_sha256": ledger.head_sha256,
        "legacy_fit_starts": ledger.protocol.legacy.published_fit_starts,
        "legacy_fit_completions": ledger.protocol.legacy.published_fit_completions,
        "legacy_budget_available": 0,
        "charged_new_attempts": len(starts),
        "charged_new_fit_attempts": fit_starts,
        "completed_new_attempts": sum(e.result == "completed" for e in ends),
        "failed_new_attempts": sum(e.result == "failed" for e in ends),
        "unresolved_new_attempts": len(starts) - len(ends),
        "remaining_new_attempts": ledger.protocol.maximum_new_attempts - len(starts),
        "remaining_new_fit_attempts": ledger.protocol.maximum_new_fit_attempts - fit_starts,
        "unknown_new_wall_costs": sum(
            (end := ended.get(e.reservation_id)) is None or end.cost is None for e in starts
        ),
        "unknown_new_fit_resource_costs": sum(
            plans[str(e.operation_id)].action in ("model_fit", "calibrator_fit")
            and (
                (end := ended.get(e.reservation_id)) is None
                or end.cost is None
                or end.cost.peak_process_tree_rss_bytes is None
                or end.cost.artifact_bytes is None
            )
            for e in starts
        ),
        "unknown_historical_cost": True,
        "selection_frozen": any(e.kind == "selection_frozen" for e in ledger.events),
        "execution_closed": any(e.kind == "closed" for e in ledger.events),
        "stage_ready": False,
    }
