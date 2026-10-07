"""Durable pre-read reservations; no complete-machine audit or evaluation permission."""

import fcntl
import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.development_contract import DevelopmentProtocol
from retailops_ai.evaluation_campaign.outcome_contract import (
    HistoricalOutcomeInventory,
    HistoricalOutcomeProtocol,
    OutcomeAccessBinding,
    OutcomeAccessEvent,
    OutcomeAccessPlan,
    OutcomeJournal,
    OutcomeJournalPolicy,
    OutcomePopulation,
)
from retailops_ai.evaluation_campaign.partition_contract import (
    DevelopmentRole,
    ForecastPartitionManifest,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.trial_contract import TrialLedger
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    directory_fd,
    read_bytes,
    regular_file,
)

MAX_JOURNAL_BYTES = 4 * 1024**2
LiteralResult = Literal["completed", "failed"]


def audit_code() -> str:
    """Pin journal/metadata validation separately from the full execution-plan runtime."""
    root = files("retailops_ai")
    names = (
        "evaluation_campaign/outcome_contract.py",
        "evaluation_campaign/outcome_journal.py",
        "evaluation_campaign/outcome_cli.py",
        "evaluation_campaign/partition_contract.py",
        "evaluation_campaign/partitions.py",
        "evaluation_campaign/trial_contract.py",
        "evaluation_campaign/development_contract.py",
        "evaluation_campaign/contract.py",
        "data_contracts/common.py",
        "data_contracts/identity.py",
        "source_snapshot/files.py",
        "forecasting/contract.py",
        "forecasting/manifest_contract.py",
        "forecasting/functional_contract.py",
        "forecasting/quality_v2_contract.py",
        "tensorflow_challenger/contract.py",
    )
    return canonical_sha256(
        {name: hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest() for name in names}
    )


def forecast_population(
    manifest: ForecastPartitionManifest,
    role: DevelopmentRole,
    *,
    outcome_artifact_sha256: str,
) -> OutcomePopulation:
    """Bind verified partition metadata; label bytes/maturity need a separate reader."""
    manifest = ForecastPartitionManifest.model_validate_json(
        canonical_bytes(manifest.model_dump(mode="json"))
    )
    if role not in ("train", "early_stopping", "tune", "calibration", "development_evaluation"):
        raise SnapshotError("outcome_journal_unsupported_role")
    descriptor = manifest.descriptor
    feature = descriptor.feature_descriptor
    window = next(r for r in descriptor.policy.roles if r.role == role)
    return OutcomePopulation.model_validate_json(
        canonical_bytes(
            {
                "data_seed": feature.source_parameters.get("seed"),
                "source_dataset_id": feature.parent.source_dataset_id,
                "snapshot_id": feature.parent.snapshot_id,
                "curated_dataset_id": feature.parent.curated_dataset_id,
                "feature_set_id": descriptor.feature_set_id,
                "partition_id": manifest.partition_id,
                "role": role,
                "origins": window.origins.model_dump(mode="json"),
                "label_knowledge_cutoff": window.label_knowledge_cutoff.isoformat(),
                "membership_keys_sha256": descriptor.populations[role].keys_sha256,
                "outcome_artifact_sha256": outcome_artifact_sha256,
            }
        )
    )


def load_history(path: Path) -> tuple[HistoricalOutcomeInventory, str]:
    """Read declarations and original metadata only; never traverse dataset paths."""
    raw = read_bytes(path.parent, path.name)
    history = HistoricalOutcomeInventory.model_validate_json(canonical_bytes(decode_json(raw)))
    verify_history(history)
    return history, hashlib.sha256(raw).hexdigest()


def _metadata(path: str, expected: str) -> bytes:
    source = Path(path)
    raw = read_bytes(source.parent, source.name)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise SnapshotError("outcome_history_metadata_changed")
    return raw


def verify_history(history: HistoricalOutcomeInventory) -> None:
    ledger = TrialLedger.model_validate_json(
        canonical_bytes(decode_json(_metadata(history.source_ledger, history.source_ledger_sha256)))
    )
    original = {a.output: a for a in ledger.plan.historical_attempts}
    if set(original) != {a.output for a in history.observed_existing_attempts}:
        raise SnapshotError("outcome_history_attempt_inventory_mismatch")
    preparation = decode_json(
        _metadata(history.source_preparation_receipt, history.source_preparation_sha256)
    )
    if (
        preparation.get("development_split_verification_reads_existing_development_labels")
        is not True
        or preparation.get("portfolio_final_test_accessed") is not False
    ):
        raise SnapshotError("outcome_history_preparation_exposure_mismatch")
    for attempt in history.observed_existing_attempts:
        previous = original[attempt.output]
        if previous.protocol_sha256 != attempt.protocol_sha256 or previous.status != attempt.status:
            raise SnapshotError("outcome_history_attempt_inventory_mismatch")
        file = previous.files.get("protocol.json")
        if file is None or file.sha256 != attempt.protocol_file_sha256:
            raise SnapshotError("outcome_history_protocol_checksum_mismatch")
        raw = _metadata(str(Path(attempt.output) / "protocol.json"), attempt.protocol_file_sha256)
        protocol = DevelopmentProtocol.model_validate_json(canonical_bytes(decode_json(raw)))
        if canonical_sha256(protocol.model_dump(mode="json")) != attempt.protocol_sha256:
            raise SnapshotError("outcome_history_protocol_identity_mismatch")
        declared = history.observed_protocols[attempt.protocol_sha256]
        actual = HistoricalOutcomeProtocol.model_validate_json(
            canonical_bytes(
                declared.model_dump(mode="json")
                | protocol.model_dump(
                    mode="json",
                    include={"parent", "feature_set_id", "split_id", "fold", "source_parameters"},
                )
            )
        )
        if actual != declared:
            raise SnapshotError("outcome_history_protocol_declaration_mismatch")


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    checked_directory(root)
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise SnapshotError("outcome_journal_private_directory_required")
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
                raise SnapshotError("outcome_journal_private_regular_lock_required")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _read(root: Path) -> OutcomeJournal:
    with regular_file(root, "journal.json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("outcome_journal_private_file_required")
        raw = stream.read(MAX_JOURNAL_BYTES + 1)
    if len(raw) > MAX_JOURNAL_BYTES:
        raise SnapshotError("outcome_journal_size_limit")
    ledger = OutcomeJournal.model_validate_json(canonical_bytes(decode_json(raw)))
    if ledger.policy.journal_path != str(root.absolute()):
        raise SnapshotError("outcome_journal_location_mismatch")
    if raw != canonical_bytes(ledger.model_dump(mode="json")) + b"\n":
        raise SnapshotError("outcome_journal_noncanonical")
    return ledger


def _publish(root: Path, ledger: OutcomeJournal) -> None:
    raw = canonical_bytes(ledger.model_dump(mode="json")) + b"\n"
    if len(raw) > MAX_JOURNAL_BYTES:
        raise SnapshotError("outcome_journal_size_limit")
    temporary = ".outcome-journal-" + uuid4().hex
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
            if (root / temporary).exists():
                os.unlink(temporary, dir_fd=root_fd)


def _runtime(ledger: OutcomeJournal) -> None:
    current = runtime_pin()
    if (
        ledger.policy.audit_code_sha256 != audit_code()
        or ledger.policy.runtime.dependency_lock_sha256 != current.dependency_lock_sha256
        or ledger.policy.runtime.python_version != current.python_version
    ):
        raise SnapshotError("outcome_journal_runtime_changed")


def initialize(root: Path, policy: OutcomeJournalPolicy) -> OutcomeJournal:
    policy = OutcomeJournalPolicy.model_validate_json(
        canonical_bytes(policy.model_dump(mode="json"))
    )
    if policy.journal_path != str(root.absolute()):
        raise SnapshotError("outcome_journal_location_mismatch")
    _runtime(
        OutcomeJournal(policy=policy, head_sha256=canonical_sha256(policy.model_dump(mode="json")))
    )
    checked_directory(root.parent)
    sources = [
        policy.historical_inventory_path,
        policy.history.source_ledger,
        policy.history.source_preparation_receipt,
        *(str(Path(a.output) / "protocol.json") for a in policy.history.observed_existing_attempts),
    ]
    protected_roots = [
        Path(policy.history.source_ledger).parent,
        *(Path(a.output) for a in policy.history.observed_existing_attempts),
    ]
    if any(Path(p).is_relative_to(root.absolute()) for p in sources) or any(
        root.absolute().is_relative_to(p) or p.is_relative_to(root.absolute())
        for p in protected_roots
    ):
        raise SnapshotError("outcome_journal_overlaps_historical_input")
    history, digest = load_history(Path(policy.historical_inventory_path))
    if history != policy.history or digest != policy.historical_inventory_file_sha256:
        raise SnapshotError("outcome_journal_history_freeze_mismatch")
    try:
        root.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        created = False
    with _locked(root):
        if (root / "journal.json").exists():
            ledger = _read(root)
            if ledger.policy != policy:
                raise SnapshotError("outcome_journal_policy_already_frozen")
            return ledger
        if not created:
            raise SnapshotError("outcome_journal_missing_history_cannot_reset")
        ledger = OutcomeJournal(
            policy=policy, head_sha256=canonical_sha256(policy.model_dump(mode="json"))
        )
        _publish(root, ledger)
        return ledger


def inspect(root: Path) -> OutcomeJournal:
    with _locked(root):
        return _read(root)


def _append(root: Path, ledger: OutcomeJournal, **values: Any) -> OutcomeJournal:
    event = OutcomeAccessEvent(
        sequence=len(ledger.events) + 1,
        previous_sha256=ledger.head_sha256,
        at=datetime.now(UTC),
        **values,
    )
    next_ledger = OutcomeJournal.model_validate_json(
        canonical_bytes(
            ledger.model_dump(mode="json")
            | {
                "events": [
                    *(e.model_dump(mode="json") for e in ledger.events),
                    event.model_dump(mode="json"),
                ],
                "head_sha256": canonical_sha256(event.model_dump(mode="json")),
            }
        )
    )
    _publish(root, next_ledger)
    return next_ledger


def register_plan(root: Path, plan: OutcomeAccessPlan) -> str:
    plan = OutcomeAccessPlan.model_validate_json(canonical_bytes(plan.model_dump(mode="json")))
    digest = canonical_sha256(plan.model_dump(mode="json"))
    with _locked(root):
        ledger = _read(root)
        _runtime(ledger)
        if plan.runtime != runtime_pin():
            raise SnapshotError("outcome_journal_execution_runtime_changed")
        plans = [e for e in ledger.events if e.kind == "plan_registered"]
        if any(e.access_plan_sha256 == digest for e in plans):
            return digest
        if len(plans) >= ledger.policy.maximum_plans:
            raise SnapshotError("outcome_journal_plan_budget_exhausted")
        _append(root, ledger, kind="plan_registered", access_plan_sha256=digest, plan=plan)
    return digest


def reserve(root: Path, plan_sha256: str, binding: OutcomeAccessBinding) -> OutcomeAccessEvent:
    binding = OutcomeAccessBinding.model_validate_json(
        canonical_bytes(binding.model_dump(mode="json"))
    )
    if binding.purpose == "independent_evaluation":
        raise SnapshotError("outcome_independent_evaluation_requires_complete_access_audit")
    digest = canonical_sha256(binding.model_dump(mode="json"))
    with _locked(root):
        ledger = _read(root)
        _runtime(ledger)
        plan = next(
            (
                e.plan
                for e in ledger.events
                if e.kind == "plan_registered" and e.access_plan_sha256 == plan_sha256
            ),
            None,
        )
        if plan is None or binding not in plan.bindings:
            raise SnapshotError("outcome_journal_unplanned_access")
        if plan.runtime != runtime_pin():
            raise SnapshotError("outcome_journal_execution_runtime_changed")
        starts = [e for e in ledger.events if e.kind == "reserved"]
        if (
            len(starts) >= ledger.policy.maximum_new_reads
            or sum(e.binding_sha256 == digest for e in starts)
            >= ledger.policy.maximum_reads_per_binding
        ):
            raise SnapshotError("outcome_journal_read_budget_exhausted")
        ledger = _append(
            root,
            ledger,
            kind="reserved",
            access_plan_sha256=plan_sha256,
            binding_sha256=digest,
            access_id="outcome-access-" + uuid4().hex,
        )
        return ledger.events[-1]


def finish(
    root: Path, access_id: str, *, result: LiteralResult, error_code: str | None = None
) -> OutcomeAccessEvent:
    with _locked(root):
        ledger = _read(root)
        start = next(
            (e for e in ledger.events if e.access_id == access_id and e.kind == "reserved"), None
        )
        if start is None:
            raise SnapshotError("outcome_journal_unknown_reservation")
        previous = next(
            (e for e in ledger.events if e.access_id == access_id and e.kind == "finished"), None
        )
        if previous is not None:
            if previous.result != result or previous.error_code != error_code:
                raise SnapshotError("outcome_journal_result_already_frozen")
            return previous
        ledger = _append(
            root,
            ledger,
            kind="finished",
            access_plan_sha256=start.access_plan_sha256,
            binding_sha256=start.binding_sha256,
            access_id=access_id,
            result=result,
            error_code=error_code,
        )
        return ledger.events[-1]


@contextmanager
def audited_access(
    root: Path, plan_sha256: str, binding: OutcomeAccessBinding
) -> Iterator[OutcomeAccessEvent]:
    """All outcome I/O/iteration belongs inside this context, including verification."""
    event = reserve(root, plan_sha256, binding)
    try:
        yield event
    except BaseException:
        finish(root, str(event.access_id), result="failed", error_code="outcome_reader_failed")
        raise
    else:
        finish(root, str(event.access_id), result="completed")


def exposure_status(ledger: OutcomeJournal, population: OutcomePopulation) -> dict[str, object]:
    """Conservative related-data inventory, not certification that new labels are fresh."""
    population = OutcomePopulation.model_validate_json(
        canonical_bytes(population.model_dump(mode="json"))
    )
    related = [
        p
        for p in ledger.policy.history.observed_protocols.values()
        if (
            p.parent.source_dataset_id == population.source_dataset_id
            or p.parent.snapshot_id == population.snapshot_id
            or p.source_parameters["seed"] == population.data_seed
        )
    ]
    plans = {e.access_plan_sha256: e.plan for e in ledger.events if e.kind == "plan_registered"}
    reads = []
    for event in ledger.events:
        if event.kind != "reserved":
            continue
        plan = plans[event.access_plan_sha256]
        if plan is None:
            raise SnapshotError("outcome_journal_unplanned_access")
        other = next(
            b.population
            for b in plan.bindings
            if canonical_sha256(b.model_dump(mode="json")) == event.binding_sha256
        )
        if (
            other.source_dataset_id == population.source_dataset_id
            or other.snapshot_id == population.snapshot_id
            or other.outcome_artifact_sha256 == population.outcome_artifact_sha256
            or other.data_seed == population.data_seed
        ):
            reads.append(event)
    return {
        "related_historical_protocols": len(related),
        "related_reserved_reads": len(reads),
        "freshness": "not_established_partial_audit",
        "existing_development_holdouts": "opened_during_parent_verification_not_untouched",
        "independent_evaluation_access_authorized": False,
    }


def summary(ledger: OutcomeJournal) -> dict[str, object]:
    starts = [e for e in ledger.events if e.kind == "reserved"]
    done = {e.access_id: e for e in ledger.events if e.kind == "finished"}
    return {
        "policy_sha256": canonical_sha256(ledger.policy.model_dump(mode="json")),
        "head_sha256": ledger.head_sha256,
        "historical_attempts": len(ledger.policy.history.observed_existing_attempts),
        "historical_protocols": len(ledger.policy.history.observed_protocols),
        "registered_plans": sum(e.kind == "plan_registered" for e in ledger.events),
        "reserved_reads": len(starts),
        "completed_reads": sum(e.result == "completed" for e in done.values()),
        "failed_reads": sum(e.result == "failed" for e in done.values()),
        "unresolved_reads": sum(e.access_id not in done for e in starts),
        "remaining_read_budget": ledger.policy.maximum_new_reads - len(starts),
        "freshness_of_unlisted_data": "unknown_not_automatically_unseen",
        "audit_scope": ledger.policy.audit_scope,
        "evaluation_status": "not_ready",
        "independent_evaluation_access_authorized": False,
        "final_test_access_authorized": False,
        "promotion_allowed": False,
    }
