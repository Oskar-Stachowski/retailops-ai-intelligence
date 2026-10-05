"""Bounded version joins within one audited complete private source replay."""

import hashlib
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from retailops_ai.curated.builder import iter_rows
from retailops_ai.curated.contract import columns_for, decoded, encoded
from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.label_contract import DemandVersion
from retailops_ai.evaluation_campaign.source_replay import _open_replayed_source_parent
from retailops_ai.evaluation_campaign.source_replay_contract import ForecastSourceReplayProtocol
from retailops_ai.evaluation_campaign.source_version_contract import (
    ForecastSourceVersion,
    ForecastSourceVersionReceipt,
)
from retailops_ai.source_snapshot.files import SnapshotError, file_hash

GRAIN = ("business_date", "product_id", "selling_location_id", "channel")
MAX_INDEX_BYTES = 128 * 1024**2
MAX_RECORD_BYTES = 32 * 1024
MAX_ROWS = 100000
MAX_VERSIONS = 8


def _check_index(db: sqlite3.Connection) -> None:
    db.commit()
    if db.execute("PRAGMA page_count").fetchone()[0] * 4096 > MAX_INDEX_BYTES:
        raise SnapshotError("forecast_source_version_index_resource_limit")


def _records(
    observation: dict[str, Any], versions: list[dict[str, Any]]
) -> Iterator[ForecastSourceVersion]:
    """Internal explicit join; these inputs alone are not a source qualification."""
    if not 1 <= len(versions) <= MAX_VERSIONS:
        raise SnapshotError("forecast_source_version_history_size_limit")
    if (
        type(observation.get("source_data_complete")) is not bool
        or observation.get("quality_status") not in {"valid", "quarantined", "incomplete"}
        or observation["curated_available_at"] is None
    ):
        raise SnapshotError("forecast_source_version_explicit_observation_quality_required")
    previous = None
    for number, row in enumerate(versions, 1):
        if (
            type(row["version"]) is not int
            or row["version"] != number
            or row["observation_id"] != observation["id"]
            or any(row[k] != observation[k] for k in GRAIN)
            or row["history_policy_version"] != "observed-quantity-history-1.0.0"
            or row["curated_available_at"] is None
            or row["available_at"] < end_of_day(row["business_date"]) + timedelta(seconds=1)
            or row["curated_available_at"] < row["available_at"]
            or previous is not None
            and row["available_at"] <= previous
        ):
            raise SnapshotError("forecast_source_version_history_identity_or_time_mismatch")
        previous = row["available_at"]
    latest = versions[-1]
    if any(
        latest[k] != observation[k]
        for k in ("observed_units", "observation_status", "available_at")
    ):
        raise SnapshotError("forecast_source_version_latest_observation_mismatch")
    for row in versions:
        is_latest = row["version"] == latest["version"]
        candidate = DemandVersion.model_validate(
            {
                k: row[k]
                for k in (
                    *GRAIN,
                    "source_record_sha256",
                    "version",
                    "curated_available_at",
                    "observed_units",
                    "observation_status",
                )
            }
            | {
                "record_id": row["id"],
                "source_data_complete": False,
                "quality_status": "incomplete",
            }
        )
        yield ForecastSourceVersion(
            candidate=candidate,
            observation_id=observation["id"],
            observation_source_record_sha256=observation["source_record_sha256"],
            quality_basis="exact_latest_observation"
            if is_latest
            else "historical_quality_not_established",
            quality_available_at=max(
                row["curated_available_at"], observation["curated_available_at"]
            )
            if is_latest
            else None,
            source_data_complete=observation["source_data_complete"] if is_latest else None,
            quality_status=observation["quality_status"] if is_latest else None,
        )


def _populate(
    db: sqlite3.Connection, curated: Path, manifest: dict[str, Any]
) -> tuple[int, int, str]:
    db.execute("PRAGMA page_size=4096")
    db.execute("PRAGMA cache_size=-4096")
    db.execute("PRAGMA temp_store=FILE")
    db.execute("PRAGMA max_page_count=32768")
    db.execute("CREATE TABLE observations(id TEXT PRIMARY KEY,body BLOB)")
    db.execute(
        "CREATE TABLE versions(observation_id TEXT,version INTEGER,body BLOB,PRIMARY KEY(observation_id,version))"
    )
    db.execute("CREATE TABLE qualified(key BLOB PRIMARY KEY,body BLOB,sha TEXT)")
    specs = {t["table"]: t for t in manifest["tables"]}
    counts = {}
    for table in ("daily_demand_observations", "daily_demand_versions"):
        count = 0
        for row in iter_rows(curated, specs[table]["files"], 256):
            count += 1
            body = encoded(row)
            if count > MAX_ROWS or len(body) > 65536:
                raise SnapshotError("forecast_source_version_input_resource_limit")
            if table == "daily_demand_observations":
                db.execute("INSERT INTO observations VALUES (?,?)", (row["id"], body))
            else:
                db.execute(
                    "INSERT INTO versions VALUES (?,?,?)",
                    (row["observation_id"], row["version"], body),
                )
            if count % 256 == 0:
                _check_index(db)
        if count != specs[table]["row_count"] or count == 0:
            raise SnapshotError("forecast_source_version_input_count_mismatch")
        counts[table] = count
    if db.execute(
        "SELECT 1 FROM versions v LEFT JOIN observations o ON v.observation_id=o.id WHERE o.id IS NULL LIMIT 1"
    ).fetchone():
        raise SnapshotError("forecast_source_version_orphan_history")
    qualified = 0
    for identity, body in db.execute("SELECT id,body FROM observations ORDER BY id"):
        rows = db.execute(
            "SELECT body FROM versions WHERE observation_id=? ORDER BY version LIMIT 9", (identity,)
        ).fetchall()
        versions = [decoded(v[0], columns_for("daily_demand_versions")) for v in rows]
        observation = decoded(body, columns_for("daily_demand_observations"))
        for record in _records(observation, versions):
            raw = canonical_bytes(record.model_dump(mode="json"))
            if len(raw) > MAX_RECORD_BYTES:
                raise SnapshotError("forecast_source_version_record_resource_limit")
            key = canonical_bytes(
                [record.candidate.model_dump(mode="json")[k] for k in (*GRAIN, "version")]
            )
            db.execute(
                "INSERT INTO qualified VALUES (?,?,?)", (key, raw, hashlib.sha256(raw).hexdigest())
            )
            qualified += 1
            if qualified % 256 == 0:
                _check_index(db)
    if qualified != counts["daily_demand_versions"]:
        raise SnapshotError("forecast_source_version_output_count_mismatch")
    _check_index(db)
    digest = hashlib.sha256()
    for (body,) in db.execute("SELECT body FROM qualified ORDER BY key"):
        digest.update(body + b"\n")
    db.execute("PRAGMA query_only=ON")
    return counts["daily_demand_observations"], qualified, digest.hexdigest()


class ForecastSourceVersionReader:
    """Rows exist only inside the audit; a receipt appears after successful exit."""

    def __init__(self, db: sqlite3.Connection, path: Path) -> None:
        self._db, self._path = db, path
        self._active = True
        self._identity = self._stat()
        self._seal = file_hash(path.parent, path.name)
        self._receipt: ForecastSourceVersionReceipt | None = None

    def _stat(self) -> tuple[int, int, int, int, int]:
        info = self._path.lstat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def _check(self) -> None:
        if not self._active:
            raise SnapshotError("forecast_source_version_reader_outside_audit_context")
        if (
            self._stat() != self._identity
            or file_hash(self._path.parent, self._path.name) != self._seal
        ):
            raise SnapshotError("forecast_source_version_private_index_changed")

    def rows(self) -> Iterator[ForecastSourceVersion]:
        self._check()
        cursor = self._db.execute("SELECT key,body,sha FROM qualified ORDER BY key")
        try:
            while True:
                if not self._active:
                    raise SnapshotError("forecast_source_version_reader_outside_audit_context")
                entry = cursor.fetchone()
                if entry is None:
                    break
                key, raw, checksum = entry
                if (
                    self._stat() != self._identity
                    or len(raw) > MAX_RECORD_BYTES
                    or hashlib.sha256(raw).hexdigest() != checksum
                ):
                    raise SnapshotError("forecast_source_version_private_index_changed")
                record = ForecastSourceVersion.model_validate_json(raw)
                if (
                    canonical_bytes(
                        [record.candidate.model_dump(mode="json")[k] for k in (*GRAIN, "version")]
                    )
                    != key
                ):
                    raise SnapshotError("forecast_source_version_private_key_mismatch")
                yield record
        finally:
            if self._active:
                cursor.close()
        self._check()

    def receipt(self) -> ForecastSourceVersionReceipt:
        if self._receipt is None:
            raise SnapshotError("forecast_source_version_receipt_requires_successful_audit_exit")
        return self._receipt


@contextmanager
def open_forecast_source_versions(
    snapshot: Path,
    curated: Path,
    protocol: ForecastSourceReplayProtocol,
    *,
    journal: Path,
    plan_sha256: str,
) -> Iterator[ForecastSourceVersionReader]:
    """Reserve five roles, replay once, join all versions before yielding any row.

    This reads the complete parents, including late and out-of-role targets.
    It neither binds physical role keys nor exports scoped outcome evidence.
    """
    with _open_replayed_source_parent(
        snapshot, curated, protocol, journal=journal, plan_sha256=plan_sha256
    ) as (private_curated, manifest, source_receipt, check_parents):
        with tempfile.TemporaryDirectory(
            prefix="ai09-source-versions-", dir=private_curated.parent
        ) as temporary:
            path = Path(temporary) / "versions.sqlite"
            with closing(sqlite3.connect(path)) as db:
                path.chmod(0o600)
                observations, versions, content_sha = _populate(db, private_curated, manifest)
                # Joins must not bridge a source/runtime mutation before callers
                # can see a single row. Reuse the context's exact allowlist seals.
                check_parents()
                reader = ForecastSourceVersionReader(db, path)
                try:
                    yield reader
                    reader._check()
                    receipt = ForecastSourceVersionReceipt(
                        source_replay=source_receipt,
                        observation_rows=observations,
                        version_rows=versions,
                        latest_observation_quality_proofs=observations,
                        historical_versions_without_quality_proof=versions - observations,
                        version_inventory_sha256=content_sha,
                    )
                finally:
                    reader._active = False
    reader._receipt = receipt
