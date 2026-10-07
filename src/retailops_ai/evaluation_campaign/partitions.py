"""Prepare complete role-specific forecast keys without opening split/label datasets."""

import hashlib
import os
import platform
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import timedelta
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from retailops_ai.data_contracts.common import DateWindow, ForecastKey, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.partition_contract import (
    ALL_ROLES,
    ROLES,
    ForecastPartitionManifest,
    ForecastPartitionPolicy,
    PartitionDescriptor,
    PartitionFile,
    PartitionMembership,
    PartitionRole,
    Purpose,
    RoleWindow,
)
from retailops_ai.forecasting.calendar import load_calendar
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifest_contract import FeatureManifest
from retailops_ai.forecasting.manifests import input_models, verify_feature_set
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    inventory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.protocol import resource_bytes
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

# The complete installed-module pin grows with independent adapter packages.
# Keep a bounded envelope while retaining every transitive module checksum.
MAX_MANIFEST_BYTES = 128 * 1024
MAX_MEMBERSHIP_BYTES = 4096
KEY_FIELDS = tuple(ForecastKey.model_fields)


def chronological_policy(
    window: DateWindow,
    *,
    train_days: int = 30,
    other_role_days: int = 10,
    purge_days: int = 15,
    label_delay_days: int = 1,
) -> ForecastPartitionPolicy:
    """Explicit lengths, never shrink roles or the purge to make an old fixture fit."""
    if any(type(n) is not int or n < 1 for n in (train_days, other_role_days)):
        raise SnapshotError("forecast_partition_positive_role_lengths_required")
    if type(purge_days) is not int or not 15 <= purge_days <= 90:
        raise SnapshotError("forecast_partition_invalid_purge")
    if type(label_delay_days) is not int or not 1 <= label_delay_days <= 30:
        raise SnapshotError("forecast_partition_invalid_label_delay")
    needed = train_days + 4 * other_role_days + 4 * purge_days
    if (window.end - window.start).days + 1 < needed:
        raise SnapshotError("forecast_partition_origin_window_too_short")
    roles = []
    start = window.start
    for role in ROLES:
        end = start + timedelta(days=(train_days if role == "train" else other_role_days) - 1)
        roles.append(
            RoleWindow(
                role=role,
                origins=DateWindow(start=start, end=end),
                label_knowledge_cutoff=end_of_day(end + timedelta(days=14 + label_delay_days)),
            )
        )
        start = end + timedelta(days=purge_days + 1)
    return ForecastPartitionPolicy(
        roles=tuple(roles),
        purge_days=purge_days,
        label_delay_days=label_delay_days,
    )


def runtime_pin() -> PreparationRuntime:
    root = files("retailops_ai")
    hashes: dict[str, str] = {}

    def visit(directory: Traversable, prefix: str = "") -> None:
        for entry in sorted(directory.iterdir(), key=lambda e: e.name):
            name = prefix + entry.name
            if entry.is_dir() and entry.name != "__pycache__":
                visit(entry, name + "/")
            elif entry.is_file() and entry.name.endswith(".py"):
                hashes[name] = hashlib.sha256(entry.read_bytes()).hexdigest()

    # Pin all installed source modules, including transitive parent verification.
    # Merely reading bytes does not import TensorFlow, estimators or a producer.
    visit(root)
    return PreparationRuntime(
        code_files=hashes,
        code_sha256=canonical_sha256(hashes),
        dependency_lock_sha256=hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
        python_version=platform.python_version(),
    )


def require_role_purpose(role: PartitionRole, purpose: Purpose) -> None:
    """A role guard, not authorization to fit or to read any outcomes."""
    expected = {
        "preprocessing_fit": "train",
        "model_fit": "train",
        "early_stopping": "early_stopping",
        "recipe_selection": "tune",
        "calibrator_fit": "calibration",
        "independent_evaluation": "development_evaluation",
    }
    if role not in ALL_ROLES or purpose not in expected or expected[purpose] != role:
        raise SnapshotError("forecast_partition_role_purpose_mismatch")
    if purpose == "independent_evaluation":
        raise SnapshotError("forecast_partition_evaluation_requires_outcome_access_audit")


def membership_key(row: ForecastKey) -> bytes:
    value = row.model_dump(mode="json")
    return canonical_bytes([value[name] for name in KEY_FIELDS])


def _membership(row: InputRow, policy: ForecastPartitionPolicy) -> PartitionMembership:
    role = policy.role_for(row.forecast_origin.date())
    return PartitionMembership(
        **row.model_dump(include=set(KEY_FIELDS)),
        role=role,
        label_knowledge_cutoff=policy.cutoff_for(role),
        feature_row_sha256=canonical_sha256(row.model_dump(mode="json")),
    )


def _index(
    db: sqlite3.Connection,
    features: Path,
    policy: ForecastPartitionPolicy,
) -> FeatureManifest:
    # Check temporal feasibility from metadata before scanning feature/history values.
    calendar = load_calendar(features / "inputs/calendar_manifest.json")
    window = calendar.descriptor.origin_window
    if policy.roles[0].origins.start < window.start or policy.roles[-1].origins.end > window.end:
        raise SnapshotError("forecast_partition_window_outside_calendar")
    feature = verify_feature_set(features)
    if feature.descriptor.row_count > policy.max_population_rows:
        raise SnapshotError("forecast_partition_population_budget")
    db.execute("PRAGMA cache_size=-4096")
    db.execute("PRAGMA temp_store=FILE")
    db.execute(
        "CREATE TABLE population(key BLOB PRIMARY KEY,role TEXT,body BLOB,seen INTEGER DEFAULT 0)"
    )
    count, total = 0, 0
    for row in input_models(features, "features"):
        if not isinstance(row, InputRow):
            raise SnapshotError("forecast_partition_feature_schema_mismatch")
        member = _membership(row, policy)
        body = canonical_bytes(member.model_dump(mode="json")) + b"\n"
        count += 1
        total += len(body)
        if (
            len(body) > MAX_MEMBERSHIP_BYTES
            or count > policy.max_population_rows
            or total > policy.max_artifact_bytes
        ):
            raise SnapshotError("forecast_partition_population_budget")
        try:
            db.execute(
                "INSERT INTO population(key,role,body) VALUES (?,?,?)",
                (membership_key(member), member.role, body),
            )
        except sqlite3.IntegrityError:
            raise SnapshotError("forecast_partition_duplicate_feature_key") from None
        if count % 256 == 0:
            _check_index(db, policy)
    _check_index(db, policy)
    if count != feature.descriptor.row_count:
        raise SnapshotError("forecast_partition_feature_row_count_mismatch")
    counts = dict(db.execute("SELECT role,COUNT(*) FROM population GROUP BY role"))
    if any(counts.get(role, 0) == 0 for role in ROLES):
        raise SnapshotError("forecast_partition_empty_role")
    return feature


def _check_index(db: sqlite3.Connection, policy: ForecastPartitionPolicy) -> None:
    db.commit()
    pages = db.execute("PRAGMA page_count").fetchone()[0]
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    if pages * page_size > policy.max_index_bytes:
        raise SnapshotError("forecast_partition_disk_index_budget")


def _path(role: PartitionRole) -> str:
    return "memberships/" + role + ".jsonl"


def _write(path: Path, raw: bytes) -> None:
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _write_role(db: sqlite3.Connection, root: Path, role: PartitionRole) -> PartitionFile:
    count, size = 0, 0
    digest, keys = hashlib.sha256(), hashlib.sha256()
    path = root / _path(role)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        for key, body in db.execute(
            "SELECT key,body FROM population WHERE role=? ORDER BY key", (role,)
        ):
            stream.write(body)
            digest.update(body)
            keys.update(key + b"\n")
            count += 1
            size += len(body)
        stream.flush()
        os.fsync(stream.fileno())
    return PartitionFile(
        row_count=count, size_bytes=size, sha256=digest.hexdigest(), keys_sha256=keys.hexdigest()
    )


def prepare_partitions(features: Path, policy: ForecastPartitionPolicy, output_root: Path) -> Path:
    policy = ForecastPartitionPolicy.model_validate_json(policy.model_dump_json())
    features, output_root = features.absolute(), output_root.absolute()
    if ".." in output_root.parts or any(
        p.is_symlink() for p in (output_root, *output_root.parents)
    ):
        raise SnapshotError("forecast_partition_unsafe_output_root")
    if output_root.is_relative_to(features) or features.is_relative_to(output_root):
        raise SnapshotError("forecast_partition_output_overlaps_features")
    pinned_runtime = runtime_pin()
    # Feasibility comes before creating the publication root.
    with tempfile.TemporaryDirectory(prefix="ai09-partition-index-") as temporary:
        with sqlite3.connect(Path(temporary) / "index.sqlite") as db:
            feature = _index(db, features, policy)
            output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            checked_directory(output_root)
            if output_root.stat().st_mode & 0o077:
                raise SnapshotError("forecast_partition_private_output_root_required")
            with tempfile.TemporaryDirectory(prefix=".partitions-", dir=output_root) as staging:
                root = Path(staging)
                (root / "memberships").mkdir(mode=0o700)
                populations = {role: _write_role(db, root, role) for role in ALL_ROLES}
                descriptor = PartitionDescriptor(
                    policy=policy,
                    feature_set_id=feature.feature_set_id,
                    feature_descriptor=feature.descriptor,
                    runtime=pinned_runtime,
                    populations=populations,
                )
                manifest = ForecastPartitionManifest(
                    partition_id="ai09-partitions-sha256-"
                    + canonical_sha256(descriptor.model_dump(mode="json")),
                    descriptor=descriptor,
                )
                raw = canonical_bytes(manifest.model_dump(mode="json")) + b"\n"
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise SnapshotError("forecast_partition_manifest_budget")
                _write(root / "manifest.json", raw)
                if verify_feature_set(features) != feature:
                    raise SnapshotError("forecast_partition_features_changed_during_prepare")
                if runtime_pin() != pinned_runtime:
                    raise SnapshotError("forecast_partition_runtime_changed_during_prepare")
                fsync_tree(root)
                destination = output_root / manifest.partition_id
                try:
                    publish_noreplace(root, destination)
                except FileExistsError:
                    if verify_partitions(features, destination) != manifest:
                        raise SnapshotError("forecast_partition_publication_conflict") from None
                return destination


def _verify_role(
    db: sqlite3.Connection,
    root: Path,
    role: PartitionRole,
    reference: PartitionFile,
) -> None:
    digest, keys = hashlib.sha256(), hashlib.sha256()
    count, size = 0, 0
    previous: bytes | None = None
    with regular_file(root, _path(role)) as stream:
        while raw := stream.readline(MAX_MEMBERSHIP_BYTES + 1):
            count += 1
            size += len(raw)
            if (
                len(raw) > MAX_MEMBERSHIP_BYTES
                or size > reference.size_bytes
                or count > reference.row_count
            ):
                raise SnapshotError("forecast_partition_membership_budget")
            member = PartitionMembership.model_validate_json(canonical_bytes(decode_json(raw)))
            key = membership_key(member)
            expected = db.execute("SELECT body,seen FROM population WHERE key=?", (key,)).fetchone()
            if (
                member.role != role
                or previous is not None
                and key <= previous
                or expected is None
                or expected[0] != raw
                or expected[1]
                or canonical_bytes(member.model_dump(mode="json")) + b"\n" != raw
            ):
                raise SnapshotError("forecast_partition_membership_mismatch")
            db.execute("UPDATE population SET seen=1 WHERE key=?", (key,))
            previous = key
            digest.update(raw)
            keys.update(key + b"\n")
    actual = PartitionFile(
        row_count=count, size_bytes=size, sha256=digest.hexdigest(), keys_sha256=keys.hexdigest()
    )
    if actual != reference:
        raise SnapshotError("forecast_partition_file_checksum_or_population_mismatch")


def verify_partitions(features: Path, root: Path) -> ForecastPartitionManifest:
    raw = read_bytes(root, "manifest.json", MAX_MANIFEST_BYTES)
    manifest = ForecastPartitionManifest.model_validate_json(canonical_bytes(decode_json(raw)))
    if raw != canonical_bytes(manifest.model_dump(mode="json")) + b"\n":
        raise SnapshotError("forecast_partition_noncanonical_manifest")
    if manifest.descriptor.runtime != runtime_pin():
        raise SnapshotError("forecast_partition_runtime_mismatch")
    inventory(root, {"manifest.json", *(_path(role) for role in ALL_ROLES)})
    with tempfile.TemporaryDirectory(prefix="ai09-partition-verify-") as temporary:
        with sqlite3.connect(Path(temporary) / "index.sqlite") as db:
            feature = _index(db, features, manifest.descriptor.policy)
            if (
                feature.feature_set_id != manifest.descriptor.feature_set_id
                or feature.descriptor != manifest.descriptor.feature_descriptor
            ):
                raise SnapshotError("forecast_partition_feature_parent_mismatch")
            for role in ALL_ROLES:
                _verify_role(db, root, role, manifest.descriptor.populations[role])
            if db.execute("SELECT COUNT(*) FROM population WHERE seen=0").fetchone()[0]:
                raise SnapshotError("forecast_partition_missing_feature_keys")
    if verify_feature_set(features) != feature:
        raise SnapshotError("forecast_partition_features_changed_during_verify")
    if runtime_pin() != manifest.descriptor.runtime:
        raise SnapshotError("forecast_partition_runtime_changed_during_verify")
    return manifest


def role_memberships(
    features: Path,
    root: Path,
    *,
    role: PartitionRole,
    purpose: Purpose,
) -> Iterator[PartitionMembership]:
    require_role_purpose(role, purpose)
    manifest = verify_partitions(features, root)
    reference = manifest.descriptor.populations[role]
    # Verify a private disk copy before returning any membership. This avoids
    # materializing a role in RAM and never accepts a label path.
    with tempfile.TemporaryFile(prefix="ai09-role-keys-") as copy:
        digest, size = hashlib.sha256(), 0
        with regular_file(root, _path(role)) as stream:
            while chunk := stream.read(64 * 1024):
                size += len(chunk)
                if size > reference.size_bytes:
                    raise SnapshotError("forecast_partition_changed_during_role_read")
                copy.write(chunk)
                digest.update(chunk)
        if size != reference.size_bytes or digest.hexdigest() != reference.sha256:
            raise SnapshotError("forecast_partition_changed_during_role_read")
        copy.seek(0)
        while line := copy.readline(MAX_MEMBERSHIP_BYTES + 1):
            yield PartitionMembership.model_validate_json(line)


def readiness(manifest: ForecastPartitionManifest) -> dict[str, object]:
    return {
        "partition_id": manifest.partition_id,
        "preparation_status": "verified",
        "evaluation_status": "not_ready",
        "labels_accessed": False,
        "development_evaluation_access_authorized": False,
        "final_test_access_authorized": False,
        "promotion_allowed": False,
        "role_counts": {r: f.row_count for r, f in manifest.descriptor.populations.items()},
        "blockers": [
            "role_scoped_label_reader_and_maturity_qualification_not_implemented",
            "outcome_access_audit_and_freshness_not_bound",
            "five_role_training_protocol_and_budget_not_bound",
            "candidate_calibrator_thresholds_and_evaluation_rules_not_frozen",
            "larger_profile_resources_and_ai07_ai08_acceptance_not_bound",
        ],
    }
