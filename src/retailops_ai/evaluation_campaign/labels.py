"""Reserve before all input I/O; expose only a fully checked private role snapshot."""

import hashlib
import os
import sqlite3
import stat
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import timedelta
from pathlib import Path

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.label_contract import (
    DemandVersion,
    ForecastOutcomeReadProtocol,
    LabelReason,
    OutcomeEvidence,
    OutcomeEvidenceManifest,
    QualifiedForecastOutcome,
    RoleLabel,
)
from retailops_ai.evaluation_campaign.outcome_contract import OutcomeAccessBinding
from retailops_ai.evaluation_campaign.outcome_journal import audited_access, inspect
from retailops_ai.evaluation_campaign.partition_contract import PartitionMembership
from retailops_ai.evaluation_campaign.partitions import (
    KEY_FIELDS,
    MAX_MEMBERSHIP_BYTES,
    membership_key,
    runtime_pin,
    verify_partitions,
)
from retailops_ai.forecasting.features import latest
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.manifest_contract import FeaturePolicy
from retailops_ai.forecasting.manifests import input_models
from retailops_ai.forecasting.splits import history_index
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_bytes,
    regular_file,
)


def _bounded_hash(root: Path, name: str, maximum: int) -> tuple[int, str]:
    count, digest = 0, hashlib.sha256()
    with regular_file(root, name) as stream:
        while chunk := stream.read(64 * 1024):
            count += len(chunk)
            if count > maximum:
                raise SnapshotError("forecast_outcome_physical_resource_limit")
            digest.update(chunk)
    return count, digest.hexdigest()


def _parent_seal(root: Path) -> dict[str, tuple[int, str]]:
    """Bound physical replay without retaining parent rows or accepting a trusted seal."""
    checked_directory(root)
    names: set[str] = set()
    size, entries = 0, 0
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            entries += 1
            if entries > 8192:
                raise SnapshotError("forecast_outcome_public_parent_resource_limit")
            path = Path(base) / name
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode):
                raise SnapshotError("forecast_outcome_unsafe_public_parent")
            names.add(path.relative_to(root).as_posix())
            size += info.st_size
            if len(names) > 4096 or size > 256 * 1024**2:
                raise SnapshotError("forecast_outcome_public_parent_resource_limit")
    inventory(root, names)
    seals = {}
    size = 0
    for name in sorted(names):
        seals[name] = _bounded_hash(root, name, 256 * 1024**2 - size)
        size += seals[name][0]
    return seals


def qualify_evidence(
    evidence: OutcomeEvidence,
    membership: PartitionMembership,
    row: InputRow,
    history: HistoryContext,
    feature_policy: FeaturePolicy,
    label_delay_days: int,
) -> QualifiedForecastOutcome:
    """Keep incomplete latest versions, late revisions and zero separate from missing."""
    if (
        membership.role == "purged"
        or membership.label_knowledge_cutoff is None
        or membership_key(evidence.key) != membership_key(membership)
        or membership_key(row) != membership_key(membership)
        or canonical_sha256(row.model_dump(mode="json")) != membership.feature_row_sha256
        or history.content_sha256() != row.history_context_sha256
    ):
        raise SnapshotError("forecast_outcome_qualification_parent_mismatch")
    cutoff = membership.label_knowledge_cutoff
    known = [
        v.model_dump()
        for v in evidence.candidates
        if v.curated_available_at is not None and v.curated_available_at <= cutoff
    ]
    # Existing forecast selection rejects ambiguous highest known versions.
    selected = latest(known, ("business_date", "product_id", "selling_location_id", "channel"))
    fact = selected[0] if selected else None
    maturity = end_of_day(row.target_date + timedelta(days=label_delay_days))
    reason: LabelReason | None = (
        "missing_or_unavailable"
        if fact is None
        else "not_mature"
        if cutoff < maturity or fact["curated_available_at"] < end_of_day(row.target_date)
        else "incomplete_source"
        if not fact["source_data_complete"]
        or fact["quality_status"] != "valid"
        or fact["observed_units"] is None
        or fact["observation_status"] == "missing"
        else None
    )
    label = RoleLabel(
        **row.model_dump(include=set(KEY_FIELDS)),
        role=membership.role,
        knowledge_cutoff=cutoff,
        label_delay_days=label_delay_days,
        maturity_not_before=maturity,
        status="eligible" if reason is None else "censored",
        observed_sales_units=fact["observed_units"] if reason is None and fact else None,
        selected_version=DemandVersion.model_validate(fact) if fact is not None else None,
        reason=reason,
    )
    reasons = []
    if (
        len(history.points) < feature_policy.minimum_active_history_days
        or row.history_known_days < feature_policy.minimum_known_history_days
    ):
        reasons.append("insufficient_history")
    known_days = [p.business_date for p in history.points if p.status != "missing"]
    if (
        known_days
        and (row.forecast_origin.date() - max(known_days)).days
        > feature_policy.maximum_observation_age_days
    ) or (not known_days and len(history.points) >= feature_policy.minimum_active_history_days):
        reasons.append("stale_history")
    opening = next(v.value for v in row.values if v.name == "target_location_open")
    if opening is None:
        reasons.append("unknown_calendar")
    elif opening is False:
        reasons.append("closed_target")
    if label.status == "censored":
        reasons.append("censored_label")
    return QualifiedForecastOutcome.model_validate(
        {
            "label": label,
            "feature_row_sha256": membership.feature_row_sha256,
            "eligible": not reasons,
            "reasons": tuple(reasons),
        }
    )


def _copy_evidence(root: Path, manifest: OutcomeEvidenceManifest, destination: Path) -> None:
    expected = canonical_bytes(manifest.model_dump(mode="json")) + b"\n"
    if read_bytes(root, "manifest.json", 8192) != expected:
        raise SnapshotError("forecast_outcome_evidence_manifest_mismatch")
    inventory(root, {"manifest.json", "outcomes.jsonl"})
    count, digest = 0, hashlib.sha256()
    with regular_file(root, "outcomes.jsonl") as source, destination.open("xb") as target:
        while chunk := source.read(64 * 1024):
            count += len(chunk)
            if count > manifest.size_bytes:
                raise SnapshotError("forecast_outcome_evidence_size_limit")
            digest.update(chunk)
            target.write(chunk)
    if (
        count != manifest.size_bytes
        or digest.hexdigest() != manifest.population.outcome_artifact_sha256
    ):
        raise SnapshotError("forecast_outcome_evidence_checksum_mismatch")


def _check_index(db: sqlite3.Connection, protocol: ForecastOutcomeReadProtocol) -> None:
    db.commit()
    if db.execute("PRAGMA page_count").fetchone()[0] * 4096 > protocol.policy.max_index_bytes:
        raise SnapshotError("forecast_outcome_index_resource_limit")


class ForecastOutcomeReader:
    """Context-bound iterator over a verified snapshot, never a caller-trusted cache."""

    def __init__(self, db: sqlite3.Connection, path: Path, max_record_bytes: int) -> None:
        self._db, self._path, self._maximum = db, path, max_record_bytes
        self._active = True
        self._identity = self._stat()
        self._seal = file_hash(path.parent, path.name)

    def _stat(self) -> tuple[int, int, int, int, int]:
        info = self._path.lstat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def _check(self) -> None:
        if not self._active:
            raise SnapshotError("forecast_outcome_reader_outside_audit_context")
        if (
            self._stat() != self._identity
            or file_hash(self._path.parent, self._path.name) != self._seal
        ):
            raise SnapshotError("forecast_outcome_private_snapshot_changed")

    def rows(self) -> Iterator[QualifiedForecastOutcome]:
        self._check()
        cursor = self._db.execute("SELECT key,body,sha FROM qualified ORDER BY key")
        try:
            while True:
                if not self._active:
                    raise SnapshotError("forecast_outcome_reader_outside_audit_context")
                entry = cursor.fetchone()
                if entry is None:
                    break
                key, raw, checksum = entry
                if (
                    self._stat() != self._identity
                    or len(raw) > self._maximum
                    or hashlib.sha256(raw).hexdigest() != checksum
                ):
                    raise SnapshotError("forecast_outcome_private_snapshot_changed")
                row = QualifiedForecastOutcome.model_validate_json(raw)
                if membership_key(row.label) != key:
                    raise SnapshotError("forecast_outcome_private_key_mismatch")
                yield row
        finally:
            # Closing the outer context already closes its connection/cursors.
            # Check active before any fetch or close on an escaped iterator.
            if self._active:
                cursor.close()
        self._check()

    def summary(self) -> dict[str, object]:
        self._check()
        counts: Counter[str] = Counter()
        digest = hashlib.sha256()
        for row in self.rows():
            counts["rows"] += 1
            counts["eligible"] += int(row.eligible)
            counts["label_" + row.label.status] += 1
            for reason in row.reasons:
                counts[reason] += 1
            digest.update(canonical_bytes(row.model_dump(mode="json")) + b"\n")
        return {
            "counts": dict(sorted(counts.items())),
            "qualified_content_sha256": digest.hexdigest(),
            "source_qualification": "not_established",
            "evaluation_status": "not_ready",
            "freshness": "not_asserted_partial_access_audit",
            "final_test_access_authorized": False,
            "promotion_allowed": False,
        }


def _populate(
    db: sqlite3.Connection,
    features: Path,
    partitions: Path,
    copy: Path,
    protocol: ForecastOutcomeReadProtocol,
    evidence: OutcomeEvidenceManifest,
) -> None:
    manifest = verify_partitions(features, partitions)
    if manifest != protocol.partitions:
        raise SnapshotError("forecast_outcome_partition_parent_mismatch")
    role = evidence.population.role
    db.execute(
        "CREATE TABLE population(key BLOB PRIMARY KEY,member BLOB,feature BLOB,seen INTEGER DEFAULT 0)"
    )
    db.execute("CREATE TABLE qualified(key BLOB PRIMARY KEY,body BLOB,sha TEXT)")
    db.execute(
        "CREATE TABLE versions(grain BLOB,version INTEGER,body BLOB,PRIMARY KEY(grain,version))"
    )
    db.execute("CREATE TABLE candidate_sets(grain BLOB PRIMARY KEY,sha TEXT)")
    keys, count = hashlib.sha256(), 0
    with regular_file(partitions, "memberships/" + role + ".jsonl") as stream:
        while raw := stream.readline(MAX_MEMBERSHIP_BYTES + 1):
            if len(raw) > MAX_MEMBERSHIP_BYTES:
                raise SnapshotError("forecast_outcome_membership_resource_limit")
            member = PartitionMembership.model_validate_json(raw)
            key = membership_key(member)
            if (
                member.role != role
                or member.label_knowledge_cutoff != evidence.population.label_knowledge_cutoff
            ):
                raise SnapshotError("forecast_outcome_role_cutoff_mismatch")
            db.execute("INSERT INTO population(key,member) VALUES (?,?)", (key, raw))
            keys.update(key + b"\n")
            count += 1
            if count > evidence.row_count:
                raise SnapshotError("forecast_outcome_membership_resource_limit")
    if (
        count != evidence.row_count
        or keys.hexdigest() != evidence.population.membership_keys_sha256
    ):
        raise SnapshotError("forecast_outcome_membership_population_mismatch")
    for row in input_models(features, "features"):
        if not isinstance(row, InputRow):
            raise SnapshotError("forecast_outcome_feature_schema_mismatch")
        if manifest.descriptor.policy.role_for(row.forecast_origin.date()) == role:
            key = membership_key(row)
            result = db.execute(
                "UPDATE population SET feature=? WHERE key=? AND feature IS NULL",
                (canonical_bytes(row.model_dump(mode="json")), key),
            )
            if result.rowcount != 1:
                raise SnapshotError("forecast_outcome_feature_population_mismatch")
    if db.execute("SELECT key FROM population WHERE feature IS NULL LIMIT 1").fetchone():
        raise SnapshotError("forecast_outcome_missing_feature")
    get_history = history_index(db, features)
    _check_index(db, protocol)
    previous: bytes | None = None
    count = 0
    with copy.open("rb") as stream:
        while raw := stream.readline(protocol.policy.max_record_bytes + 1):
            count += 1
            if len(raw) > protocol.policy.max_record_bytes or count > evidence.row_count:
                raise SnapshotError("forecast_outcome_record_resource_limit")
            record = OutcomeEvidence.model_validate_json(canonical_bytes(decode_json(raw)))
            key = membership_key(record.key)
            expected = db.execute(
                "SELECT member,feature,seen FROM population WHERE key=?", (key,)
            ).fetchone()
            if (
                raw != canonical_bytes(record.model_dump(mode="json")) + b"\n"
                or previous is not None
                and key <= previous
                or expected is None
                or expected[2]
            ):
                raise SnapshotError("forecast_outcome_exact_population_mismatch")
            for version in record.candidates:
                grain = canonical_bytes(
                    [
                        version.product_id,
                        version.selling_location_id,
                        version.channel,
                        version.business_date.isoformat(),
                    ]
                )
                body = canonical_bytes(version.model_dump(mode="json"))
                prior = db.execute(
                    "SELECT body FROM versions WHERE grain=? AND version=?",
                    (grain, version.version),
                ).fetchone()
                if prior is not None and prior[0] != body:
                    raise SnapshotError("forecast_outcome_inconsistent_shared_version")
                db.execute(
                    "INSERT OR IGNORE INTO versions VALUES (?,?,?)", (grain, version.version, body)
                )
            grain = canonical_bytes(
                [
                    record.key.product_id,
                    record.key.selling_location_id,
                    record.key.channel,
                    record.key.target_date.isoformat(),
                ]
            )
            candidate_hash = canonical_sha256(
                sorted(
                    canonical_bytes(v.model_dump(mode="json")).decode() for v in record.candidates
                )
            )
            prior_set = db.execute(
                "SELECT sha FROM candidate_sets WHERE grain=?", (grain,)
            ).fetchone()
            if prior_set is not None and prior_set[0] != candidate_hash:
                raise SnapshotError("forecast_outcome_inconsistent_candidate_set")
            db.execute("INSERT OR IGNORE INTO candidate_sets VALUES (?,?)", (grain, candidate_hash))
            member = PartitionMembership.model_validate_json(expected[0])
            row = InputRow.model_validate_json(expected[1])
            qualified = qualify_evidence(
                record,
                member,
                row,
                get_history(row.history_context_sha256),
                manifest.descriptor.feature_descriptor.resolved_policy,
                manifest.descriptor.policy.label_delay_days,
            )
            body = canonical_bytes(qualified.model_dump(mode="json"))
            if len(body) > protocol.policy.max_record_bytes:
                raise SnapshotError("forecast_outcome_record_resource_limit")
            db.execute(
                "INSERT INTO qualified VALUES (?,?,?)",
                (key, body, hashlib.sha256(body).hexdigest()),
            )
            db.execute("UPDATE population SET seen=1 WHERE key=?", (key,))
            previous = key
            if count % 256 == 0:
                _check_index(db, protocol)
    if (
        count != evidence.row_count
        or db.execute("SELECT key FROM population WHERE seen=0 LIMIT 1").fetchone()
    ):
        raise SnapshotError("forecast_outcome_missing_keys")
    _check_index(db, protocol)
    db.execute("PRAGMA query_only=ON")


@contextmanager
def open_forecast_outcomes(
    features: Path,
    partitions: Path,
    evidence_root: Path,
    protocol: ForecastOutcomeReadProtocol,
    *,
    journal: Path,
    plan_sha256: str,
    binding: OutcomeAccessBinding,
) -> Iterator[ForecastOutcomeReader]:
    """A reservation covers the physical artifact plus complete public parent replay.

    Full curated/source lineage is not replayed here: its broader outcome exposure
    requires a separate exporter and audit. This reader grants no freshness proof.
    """
    binding = OutcomeAccessBinding.model_validate_json(
        canonical_bytes(binding.model_dump(mode="json"))
    )
    if binding.purpose == "independent_evaluation":
        raise SnapshotError("outcome_independent_evaluation_requires_complete_access_audit")
    protocol = ForecastOutcomeReadProtocol.model_validate_json(
        canonical_bytes(protocol.model_dump(mode="json"))
    )
    evidence = next((e for e in protocol.evidence if e.population == binding.population), None)
    if evidence is None:
        raise SnapshotError("forecast_outcome_unbound_population")
    ledger = inspect(journal)
    plan = next(
        (
            e.plan
            for e in ledger.events
            if e.kind == "plan_registered" and e.access_plan_sha256 == plan_sha256
        ),
        None,
    )
    if plan is None or plan.protocol_sha256 != canonical_sha256(protocol.model_dump(mode="json")):
        raise SnapshotError("forecast_outcome_unbound_read_protocol")
    with audited_access(journal, plan_sha256, binding):
        with tempfile.TemporaryDirectory(
            prefix="ai09-scoped-outcome-", dir=Path(tempfile.gettempdir()).resolve()
        ) as temporary:
            directory = Path(temporary)
            copy = directory / "evidence.jsonl"
            parent_seals = (_parent_seal(features), _parent_seal(partitions))
            _copy_evidence(evidence_root, evidence, copy)
            db_path = directory / "qualified.sqlite"
            db_path.touch(mode=0o600, exist_ok=False)
            with closing(sqlite3.connect(db_path)) as db:
                db.execute("PRAGMA page_size=4096")
                db.execute("PRAGMA cache_size=-4096")
                db.execute("PRAGMA temp_store=FILE")
                db.execute("PRAGMA mmap_size=0")
                db.execute(f"PRAGMA max_page_count={protocol.policy.max_index_bytes // 4096}")  # noqa: S608 - validated integer
                _populate(db, features, partitions, copy, protocol, evidence)
                if (_parent_seal(features), _parent_seal(partitions)) != parent_seals:
                    raise SnapshotError("forecast_outcome_public_parent_changed")
                reader = ForecastOutcomeReader(db, db_path, protocol.policy.max_record_bytes)
                try:
                    yield reader
                    reader._check()
                    if runtime_pin() != protocol.partitions.descriptor.runtime:
                        raise SnapshotError("forecast_outcome_runtime_changed")
                    if (_parent_seal(features), _parent_seal(partitions)) != parent_seals:
                        raise SnapshotError("forecast_outcome_public_parent_changed")
                    if (
                        _bounded_hash(evidence_root, "outcomes.jsonl", evidence.size_bytes)
                        != (evidence.size_bytes, evidence.population.outcome_artifact_sha256)
                        or read_bytes(evidence_root, "manifest.json", 8192)
                        != canonical_bytes(evidence.model_dump(mode="json")) + b"\n"
                    ):
                        raise SnapshotError("forecast_outcome_evidence_changed_during_read")
                    inventory(evidence_root, {"manifest.json", "outcomes.jsonl"})
                finally:
                    reader._active = False
