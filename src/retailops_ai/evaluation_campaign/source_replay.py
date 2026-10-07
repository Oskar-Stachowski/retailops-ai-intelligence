"""Replay the complete source parent under five durable pre-read reservations."""

import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from retailops_ai.curated.builder import (
    derive,
    implementation,
    manifest_schema_bytes,
    verify_curated,
)
from retailops_ai.curated.contract import Config, source_contract
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.labels import _bounded_hash
from retailops_ai.evaluation_campaign.outcome_contract import OutcomeAccessBinding
from retailops_ai.evaluation_campaign.outcome_journal import audited_access, inspect
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.source_replay_contract import (
    ForecastSourceReplayProtocol,
    ForecastSourceReplayReceipt,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    inventory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.importer import verify_snapshot
from retailops_ai.source_snapshot.protocol import Limits, inspect_snapshot

Seal = dict[str, tuple[int, str]]


def _inspect_parents(
    snapshot: Path, curated: Path, protocol: ForecastSourceReplayProtocol, limits: Limits
) -> tuple[set[str], set[str]]:
    """Reject truth/unlisted files using metadata, before hashing any fact bytes."""
    raw = read_bytes(snapshot, "snapshot_manifest.json")
    if hashlib.sha256(raw).hexdigest() != protocol.snapshot_manifest_sha256:
        raise SnapshotError("forecast_source_replay_frozen_manifest_mismatch")
    if decode_json(raw).get("schema_version") != protocol.schema_version:
        raise SnapshotError("forecast_source_replay_unsupported_snapshot_version")
    source = inspect_snapshot(snapshot, False, limits)
    raw = read_bytes(curated, "curated_manifest.json")
    if hashlib.sha256(raw).hexdigest() != protocol.curated_manifest_sha256:
        raise SnapshotError("forecast_source_replay_frozen_manifest_mismatch")
    document = decode_json(raw)
    try:
        Draft202012Validator(decode_json(manifest_schema_bytes(protocol.schema_version))).validate(
            document
        )
    except ValidationError as exc:
        raise SnapshotError("forecast_source_replay_invalid_curated_metadata") from exc
    if [t["table"] for t in document["tables"]] != sorted(
        source_contract(protocol.schema_version)["fact_tables"]
    ):
        raise SnapshotError("forecast_source_replay_curated_table_allowlist")
    refs = [f for t in [*document["tables"], document["quarantine"]] for f in t["files"]]
    names = {"curated_manifest.json", "manifest.sha256", *(r["path"] for r in refs)}
    if (
        len(names) != len(refs) + 2
        or len(names) > limits.max_files
        or sum(r["bytes"] for r in refs) > limits.max_bytes
        or sum(r["row_count"] for r in refs) > limits.max_rows
    ):
        raise SnapshotError("forecast_source_replay_curated_resource_limit")
    inventory(curated, names)
    return source.names, names


def _seal_parent(root: Path, names: set[str]) -> Seal:
    # Never hash a file outside the metadata allowlist, even if an extra appears
    # between inspection and copying. The inventory check itself reads no data.
    inventory(root, names)
    seal, size = {}, 0
    for name in sorted(names):
        seal[name] = _bounded_hash(root, name, 256 * 1024**2 - size)
        size += seal[name][0]
    inventory(root, names)
    return seal


def replay_bindings(protocol: ForecastSourceReplayProtocol) -> tuple[OutcomeAccessBinding, ...]:
    """Bind the whole curated manifest, never a purported role-only exposure."""
    protocol = ForecastSourceReplayProtocol.model_validate_json(
        canonical_bytes(protocol.model_dump(mode="json"))
    )
    return tuple(
        OutcomeAccessBinding(
            population=p,
            purpose="verification",
            training_initialization_seed=protocol.training_initialization_seed,
            recipe_sha256=protocol.replay_recipe_sha256,
        )
        for p in protocol.populations
    )


def _copy_parent(source: Path, destination: Path, seal: Seal) -> None:
    destination.mkdir(mode=0o700)
    for name, expected in seal.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        size, digest = 0, hashlib.sha256()
        # Each original is opened without following symlinks; copies are private.
        with regular_file(source, name) as stream:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while chunk := stream.read(64 * 1024):
                    size += len(chunk)
                    if size > expected[0]:
                        raise SnapshotError("forecast_source_replay_copy_changed")
                    digest.update(chunk)
                    output.write(chunk)
        if (size, digest.hexdigest()) != expected:
            raise SnapshotError("forecast_source_replay_copy_changed")


def logical_document(document: dict[str, Any]) -> dict[str, Any]:
    """Complete logical identity; Parquet chunk layout may legitimately differ."""
    return document | {
        "tables": [{k: v for k, v in t.items() if k != "files"} for t in document["tables"]],
        "quarantine": {k: v for k, v in document["quarantine"].items() if k != "files"},
    }


@contextmanager
def _open_replayed_source_parent(
    snapshot: Path,
    curated: Path,
    protocol: ForecastSourceReplayProtocol,
    *,
    journal: Path,
    plan_sha256: str,
) -> Iterator[tuple[Path, dict[str, Any], ForecastSourceReplayReceipt, Callable[[], None]]]:
    """Internal private source lifetime; the receipt is provisional until exit."""
    protocol = ForecastSourceReplayProtocol.model_validate_json(
        canonical_bytes(protocol.model_dump(mode="json"))
    )
    protocol_sha = canonical_sha256(protocol.model_dump(mode="json"))
    bindings = replay_bindings(protocol)
    ledger = inspect(journal)
    plan = next(
        (
            e.plan
            for e in ledger.events
            if e.kind == "plan_registered" and e.access_plan_sha256 == plan_sha256
        ),
        None,
    )
    if plan is None or plan.protocol_sha256 != protocol_sha or plan.bindings != bindings:
        raise SnapshotError("forecast_source_replay_unbound_protocol_or_exposure")
    with ExitStack() as accesses:
        # Partial reservation failure still charges already reserved roles and
        # exposes no source bytes. All five must succeed before ANY parent I/O.
        events = [accesses.enter_context(audited_access(journal, plan_sha256, b)) for b in bindings]
        with tempfile.TemporaryDirectory(
            prefix="ai09-source-replay-", dir=Path(tempfile.gettempdir()).resolve()
        ) as temporary:
            private = Path(temporary)
            snapshot, curated = checked_directory(snapshot), checked_directory(curated)
            if snapshot.is_relative_to(curated) or curated.is_relative_to(snapshot):
                raise SnapshotError("forecast_source_replay_distinct_parents_required")
            limits = Limits(
                max_bytes=protocol.policy.max_parent_bytes,
                max_files=protocol.policy.max_parent_files,
                max_rows=protocol.policy.max_rows_per_parent,
                batch_rows=protocol.policy.batch_rows,
            )
            names = _inspect_parents(snapshot, curated, protocol, limits)
            seals = (_seal_parent(snapshot, names[0]), _seal_parent(curated, names[1]))
            for seal, name, expected in (
                (seals[0], "snapshot_manifest.json", protocol.snapshot_manifest_sha256),
                (seals[1], "curated_manifest.json", protocol.curated_manifest_sha256),
            ):
                if name not in seal or seal[name][1] != expected:
                    raise SnapshotError("forecast_source_replay_frozen_manifest_mismatch")
            _copy_parent(snapshot, private / "snapshot", seals[0])
            _copy_parent(curated, private / "curated", seals[1])
            source = verify_snapshot(private / "snapshot", limits=limits, scratch=private)
            actual = verify_curated(private / "curated", limits=limits)
            descriptor = actual["descriptor"]
            if (
                source.manifest["schema_version"] != protocol.schema_version
                or actual["schema_version"] != protocol.schema_version
                or source.source_id != protocol.parent.source_dataset_id
                or source.snapshot_id != protocol.parent.snapshot_id
                or actual["curated_dataset_id"] != protocol.parent.curated_dataset_id
                or canonical_sha256(descriptor) != protocol.parent.curated_descriptor_sha256
                or descriptor["config"]["business_timezone"] != protocol.parent.business_timezone
                or actual["readiness"]["forecast_source"] != protocol.parent.forecast_source_status
                or descriptor["parent_source_dataset_id"] != source.source_id
                or descriptor["parent_snapshot_id"] != source.snapshot_id
                or descriptor["source_parameters"] != protocol.source_parameters
                or source.manifest["source"]["descriptor"]["resolved_parameters"]
                != protocol.source_parameters
            ):
                raise SnapshotError("forecast_source_replay_parent_identity_mismatch")
            if descriptor["transform"] != implementation(protocol.schema_version):
                raise SnapshotError("forecast_source_replay_transform_runtime_mismatch")
            declared_config = descriptor["config"]
            config = Config(
                currencies=tuple(declared_config["currencies"]),
                business_timezone=declared_config["business_timezone"],
                dictionary_version=declared_config["dictionary_version"],
            )
            payload, scratch = private / "replayed", private / "transform"
            payload.mkdir(mode=0o700)
            scratch.mkdir(mode=0o700)
            replayed = derive(private / "snapshot", source, payload, scratch, config, limits)
            if logical_document(actual) != logical_document(replayed):
                raise SnapshotError("forecast_source_replay_complete_logical_mismatch")
            receipt = ForecastSourceReplayReceipt(
                protocol_sha256=protocol_sha,
                access_plan_sha256=plan_sha256,
                access_ids=tuple(str(e.access_id) for e in events),
                parent=protocol.parent,
                runtime_code_sha256=plan.runtime.code_sha256,
                snapshot_inventory_sha256=canonical_sha256(seals[0]),
                curated_inventory_sha256=canonical_sha256(seals[1]),
                logical_curated_sha256=canonical_sha256(logical_document(actual)),
                source_tables=len(source.manifest["tables"]),
                source_rows=sum(t["row_count"] for t in source.manifest["tables"]),
                curated_tables=len(actual["tables"]),
                curated_rows=sum(t["row_count"] for t in actual["tables"]),
            )

            def check_parents() -> None:
                if (_seal_parent(snapshot, names[0]), _seal_parent(curated, names[1])) != seals or (
                    _seal_parent(private / "snapshot", names[0]),
                    _seal_parent(private / "curated", names[1]),
                ) != seals:
                    raise SnapshotError("forecast_source_replay_parent_changed_during_replay")
                if runtime_pin() != plan.runtime:
                    raise SnapshotError("forecast_source_replay_execution_runtime_changed")

            check_parents()
            yield private / "curated", actual, receipt, check_parents
            check_parents()


def verify_forecast_source_parent(
    snapshot: Path,
    curated: Path,
    protocol: ForecastSourceReplayProtocol,
    *,
    journal: Path,
    plan_sha256: str,
) -> ForecastSourceReplayReceipt:
    """Return after full replay, immutable-input checks and all journal finishes.

    Consumer consistency does not establish producer truth or physical role keys.
    A receipt never authorizes a later read without fresh reservation and replay.
    """
    with _open_replayed_source_parent(
        snapshot, curated, protocol, journal=journal, plan_sha256=plan_sha256
    ) as (_, _, receipt, _):
        pass
    return receipt
