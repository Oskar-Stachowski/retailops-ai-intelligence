"""Replay partition parents into a disposable, capped physical-origin join."""

import hashlib
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StrictInt, TypeAdapter

from retailops_ai.curated.builder import iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, columns_for, encoded
from retailops_ai.data_contracts.common import Contract, UtcTime, utc_time
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
    file_hash,
    json_sha256,
    read_json,
)
from retailops_ai.source_snapshot.protocol import Limits
from retailops_ai.stockout.dataset import INPUT_LIMITS
from retailops_ai.stockout.feature_contract import FeaturePolicy
from retailops_ai.stockout_history.bundle import HistoryPreparation
from retailops_ai.stockout_label_partitions.bundle import iter_verified_labels
from retailops_ai.stockout_preparation.bundle import PartitionPolicy, _check_partition
from retailops_ai.stockout_storage.store import DiskFacts, StoragePolicy
from retailops_ai.stockout_training.contract import MAX_ROWS
from retailops_ai.stockout_upstream_storage.bundle import (
    check_feature_parent,
    iter_verified_upstream,
)

MAX_DB_BYTES = 128 * 1024**2
MAX_PAYLOAD_BYTES = 64 * 1024**2
MAX_PARENT_BYTES = 128 * 1024**2
MAX_PARENT_FILES = 4096
PhysicalKey = tuple[str, str, str]
UTC_CLOCK = TypeAdapter(UtcTime)


@dataclass(frozen=True)
class PartitionInputs:
    curated: Path
    private: Path
    features: Path
    upstream: Path
    labels: Path

    def roots(self) -> dict[str, Path]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


class TemporalStoragePolicy(Contract):
    version: Literal["stockout-temporal-disk-1.0.0"] = "stockout-temporal-disk-1.0.0"
    max_rows: Annotated[StrictInt, Field(ge=1, le=MAX_ROWS)] = MAX_ROWS
    max_db_bytes: Annotated[StrictInt, Field(ge=4096, le=MAX_DB_BYTES)] = MAX_DB_BYTES
    max_payload_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_PAYLOAD_BYTES)] = MAX_PAYLOAD_BYTES
    max_batch_bytes: Annotated[StrictInt, Field(ge=1, le=16 * 1024**2)] = 16 * 1024**2
    cache_kib: Literal[8192] = 8192


DEFAULT_STORAGE_POLICY = TemporalStoragePolicy()


def physical_key(row: dict[str, Any]) -> PhysicalKey:
    return (
        row["product_id"],
        row["stock_location_id"],
        UTC_CLOCK.validate_python(row["as_of"]).isoformat(timespec="microseconds"),
    )


def capture(root: Path) -> dict[str, list[Any]]:
    """Finite content seal, including metadata, extra files and symlink rejection."""
    root = checked_directory(root)
    result: dict[str, list[Any]] = {}
    size = 0
    for path in root.rglob("*"):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise SnapshotError("stockout_temporal_unsafe_parent_entry")
        if path.is_file():
            if len(result) >= MAX_PARENT_FILES or path.stat().st_size + size > MAX_PARENT_BYTES:
                raise SnapshotError("stockout_temporal_parent_resource_limit")
            count, checksum = file_hash(root, path.relative_to(root).as_posix())
            size += count
            if size > MAX_PARENT_BYTES:
                raise SnapshotError("stockout_temporal_parent_resource_limit")
            result[path.relative_to(root).as_posix()] = [count, checksum]
    return result


class TemporalStore:
    """No caller-created cache is trusted; a context replays all three partition parents."""

    def __init__(
        self,
        inputs: PartitionInputs,
        *,
        allow_evaluation_truth: bool = False,
        policy: TemporalStoragePolicy = DEFAULT_STORAGE_POLICY,
        scratch: Path | None = None,
    ) -> None:
        self.inputs, self.allow_truth, self.policy, self.scratch = (
            inputs,
            allow_evaluation_truth,
            policy,
            scratch,
        )
        self._stack, self._used = ExitStack(), False
        self._db: sqlite3.Connection | None = None
        self.parents: dict[str, dict[str, Any]] = {}
        self.parent_seals: dict[str, dict[str, list[Any]]] = {}
        self.seal: dict[str, Any] = {}
        self.stats = dict(payload_bytes=0, maximum_batch_bytes=0, database_bytes=0)
        self.counts: dict[str, int] = {}

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("stockout_temporal_store_single_use")
        self._used = True
        try:
            self._open()
        except sqlite3.DatabaseError:
            self.__exit__()
            raise SnapshotError("stockout_temporal_database_resource_or_write_failure") from None
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        self._stack.close()
        self._db = None

    def connection(self) -> sqlite3.Connection:
        if self._db is None:
            raise SnapshotError("stockout_temporal_store_closed")
        return self._db

    def _insert(self, kind: str, point: dict[str, Any]) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        raw = canonical_json(point)
        self.stats["payload_bytes"] += len(raw)
        if (
            self.counts[kind] > self.policy.max_rows
            or self.stats["payload_bytes"] > self.policy.max_payload_bytes
        ):
            raise SnapshotError("stockout_temporal_input_resource_limit")
        try:
            self.connection().execute(
                "INSERT INTO points VALUES (?,?,?,?,?,?)",
                (kind, *physical_key(point), raw, hashlib.sha256(raw).hexdigest()),
            )
        except sqlite3.IntegrityError:
            raise SnapshotError("stockout_temporal_duplicate_physical_origin") from None

    def _open(self) -> None:
        if not self.allow_truth:
            raise SnapshotError("stockout_temporal_evaluation_truth_opt_in_required")
        scratch = checked_directory(
            self.scratch if self.scratch else Path(tempfile.gettempdir()).resolve()
        )
        for root in self.inputs.roots().values():
            source = checked_directory(root)
            if source == scratch or source in scratch.parents:
                raise SnapshotError("stockout_temporal_scratch_inside_parent")
        self.parent_seals = {n: capture(p) for n, p in self.inputs.roots().items()}
        directory = Path(
            self._stack.enter_context(
                tempfile.TemporaryDirectory(prefix=".stockout-temporal-facts-", dir=scratch)
            )
        )
        self.path = directory / "facts.sqlite"
        self.path.touch(mode=0o600, exist_ok=False)
        db = sqlite3.connect(self.path)
        self._db = db
        self._stack.callback(db.close)
        db.execute("PRAGMA page_size=4096")
        db.execute("PRAGMA cache_size=-8192")
        db.execute("PRAGMA temp_store=FILE")
        db.execute("PRAGMA mmap_size=0")
        db.execute(f"PRAGMA max_page_count={self.policy.max_db_bytes // 4096}")  # noqa: S608 - validated integer
        db.execute(
            "CREATE TABLE points(kind TEXT, product TEXT, stock TEXT, origin TEXT, body BLOB, checksum TEXT, PRIMARY KEY(kind,product,stock,origin))"
        )
        db.execute(
            "CREATE TABLE catalog(position INTEGER PRIMARY KEY, product TEXT, ready TEXT, body BLOB, checksum TEXT)"
        )
        db.execute("CREATE INDEX catalog_lookup ON catalog(product,ready)")
        self._load_features()
        for point in iter_verified_upstream(
            self.inputs.upstream, self.inputs.curated, self.inputs.features
        ):
            self._insert("upstream", point.model_dump(mode="json"))
        for label in iter_verified_labels(
            self.inputs.labels, self.inputs.private, allow_evaluation_truth=self.allow_truth
        ):
            self._insert("labels", label.model_dump(mode="json"))
        self.parents.update(
            upstream=read_json(self.inputs.upstream, "manifest.json"),
            labels=read_json(self.inputs.labels, "manifest.json"),
        )
        feature = self.parents["features"]["descriptor"]
        upstream = self.parents["upstream"]["descriptor"]
        labels = self.parents["labels"]["descriptor"]
        if (
            any(
                feature[k] != parent[k]
                for parent in (upstream, labels)
                for k in ("source_dataset_id", "qualification_id")
            )
            or upstream["feature_bundle_id"] != self.parents["features"]["feature_bundle_id"]
            or upstream["curated_dataset_id"] != feature["curated_dataset_id"]
        ):
            raise SnapshotError("stockout_temporal_parent_pin_mismatch")
        if (
            db.execute(
                "SELECT product,stock,origin FROM points WHERE kind='features' EXCEPT SELECT product,stock,origin FROM points WHERE kind='upstream'"
            ).fetchone()
            or db.execute(
                "SELECT product,stock,origin FROM points WHERE kind='upstream' EXCEPT SELECT product,stock,origin FROM points WHERE kind='features'"
            ).fetchone()
        ):
            raise SnapshotError("stockout_temporal_upstream_physical_grain_mismatch")
        self._load_catalog(directory)
        db.execute(
            "CREATE TABLE origins AS SELECT DISTINCT product,stock,origin FROM points WHERE kind IN ('features','labels')"
        )
        db.execute("CREATE UNIQUE INDEX origins_order ON origins(product,stock,origin)")
        if db.execute("SELECT COUNT(*) FROM origins").fetchone()[0] > self.policy.max_rows:
            raise SnapshotError("stockout_temporal_origin_resource_limit")
        db.commit()
        self.check_parents()
        self.stats["database_bytes"] = self.path.stat().st_size
        if self.stats["database_bytes"] > self.policy.max_db_bytes:
            raise SnapshotError("stockout_temporal_database_resource_limit")
        ordered = hashlib.sha256()
        for row in db.execute(
            "SELECT kind,product,stock,origin,checksum FROM points ORDER BY kind,product,stock,origin"
        ):
            ordered.update(canonical_json(list(row)) + b"\n")
        for row in db.execute(
            "SELECT position,product,ready,checksum FROM catalog ORDER BY position"
        ):
            ordered.update(canonical_json(list(row)) + b"\n")
        self.seal = dict(
            parent_files_sha256=json_sha256(self.parent_seals),
            indexed_payloads_sha256=ordered.hexdigest(),
            rows=dict(sorted(self.counts.items())),
        )
        self._file_seal = file_hash(directory, self.path.name)
        db.execute("PRAGMA query_only=ON")

    def _load_features(self) -> None:
        stored = read_json(self.inputs.features, "manifest.json")
        descriptor = stored["descriptor"]
        if (
            descriptor["schema_version"] != "2.2.0"
            or descriptor["role"] != "stockout_feature_partitions"
        ):
            raise SnapshotError("stockout_temporal_features_22_required")
        with DiskFacts(
            self.inputs.curated,
            policy=StoragePolicy.model_validate(descriptor["storage"]["policy"]),
        ) as facts:
            preparation = HistoryPreparation(
                facts,
                FeaturePolicy.model_validate(descriptor["feature_policy"]),
                PartitionPolicy.model_validate(descriptor["partition_policy"]),
            )
            for points, spec, blobs in preparation.partitions():
                _check_partition(self.inputs.features, spec, blobs)
                for point in points:
                    self._insert("features", point.model_dump(mode="json"))
            expected = preparation.manifest()
        if stored != expected:
            raise SnapshotError("stockout_temporal_feature_full_replay_mismatch")
        check_feature_parent(self.inputs.features, expected)
        self.parents["features"] = expected

    def _load_catalog(self, directory: Path) -> None:
        document = verify_curated(
            self.inputs.curated,
            limits=Limits(max_rows=INPUT_LIMITS.max_rows, max_bytes=INPUT_LIMITS.max_bytes),
        )
        if (
            document["curated_dataset_id"]
            != self.parents["features"]["descriptor"]["curated_dataset_id"]
        ):
            raise SnapshotError("stockout_temporal_catalog_parent_mismatch")
        spec = next(t for t in document["tables"] if t["table"] == "product_catalog")
        digest = Digest(
            directory / "catalog-seal.sqlite",
            columns_for("product_catalog", "1.1.0"),
            spec["grain"],
        )
        try:
            for position, row in enumerate(iter_rows(self.inputs.curated, spec["files"], 256)):
                digest.add(row)
                raw = encoded(row)
                self.stats["payload_bytes"] += len(raw)
                if (
                    position >= self.policy.max_rows
                    or self.stats["payload_bytes"] > self.policy.max_payload_bytes
                ):
                    raise SnapshotError("stockout_temporal_catalog_resource_limit")
                self.connection().execute(
                    "INSERT INTO catalog VALUES (?,?,?,?,?)",
                    (
                        position,
                        row["id"],
                        utc_time(row["curated_available_at"]).isoformat(timespec="microseconds")
                        if row["curated_available_at"] is not None
                        else None,
                        raw,
                        hashlib.sha256(raw).hexdigest(),
                    ),
                )
            if any(spec[k] != v for k, v in digest.summary().items()):
                raise SnapshotError("stockout_temporal_catalog_replay_mismatch")
        finally:
            digest.close()
            (directory / "catalog-seal.sqlite").unlink()

    def check_parents(self) -> None:
        if any(capture(p) != self.parent_seals[n] for n, p in self.inputs.roots().items()):
            raise SnapshotError("stockout_temporal_parent_changed")

    def check_database(self) -> None:
        self.connection()
        if file_hash(self.path.parent, self.path.name) != self._file_seal:
            raise SnapshotError("stockout_temporal_private_database_changed")

    def batches(self, batch_rows: int) -> Iterator[list[PhysicalKey]]:
        if not 1 <= batch_rows <= 256:
            raise SnapshotError("stockout_temporal_batch_resource_limit")
        self.check_database()
        cursor = self.connection().execute(
            "SELECT product,stock,origin FROM origins ORDER BY product,stock,origin"
        )
        while batch := cursor.fetchmany(batch_rows):
            self.check_database()
            yield batch

    def points(self, kind: str, keys: list[PhysicalKey]) -> list[dict[str, Any]]:
        if kind not in {"features", "upstream", "labels"} or len(keys) > 256:
            raise SnapshotError("stockout_temporal_query_resource_limit")
        self.check_database()
        result, size = [], 0
        for key in keys:
            row = (
                self.connection()
                .execute(
                    "SELECT body,checksum FROM points WHERE kind=? AND product=? AND stock=? AND origin=?",
                    (kind, *key),
                )
                .fetchone()
            )
            if row is None:
                continue
            raw, checksum = row
            size += len(raw)
            if size > self.policy.max_batch_bytes:
                raise SnapshotError("stockout_temporal_batch_resource_limit")
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise SnapshotError("stockout_temporal_payload_checksum_mismatch")
            point = decode_json(raw, limit=self.policy.max_batch_bytes)
            if physical_key(point) != key:
                raise SnapshotError("stockout_temporal_payload_index_mismatch")
            result.append(point)
        self.stats["maximum_batch_bytes"] = max(self.stats["maximum_batch_bytes"], size)
        return result

    def category(self, product: str, as_of: str) -> tuple[str, str | None]:
        self.check_database()
        ready = UTC_CLOCK.validate_python(as_of).isoformat(timespec="microseconds")
        rows = (
            self.connection()
            .execute(
                "SELECT body,checksum FROM catalog WHERE product=? AND ready<=? ORDER BY position LIMIT 2",
                (product, ready),
            )
            .fetchall()
        )
        if len(rows) > 1:
            raise SnapshotError("stockout_training_ambiguous_PIT_product_category")
        if not rows:
            return "__unknown__", None
        raw, checksum = rows[0]
        if hashlib.sha256(raw).hexdigest() != checksum:
            raise SnapshotError("stockout_temporal_catalog_checksum_mismatch")
        value = decode_json(raw, limit=self.policy.max_batch_bytes)
        if value["id"] != product or UTC_CLOCK.validate_python(
            value["curated_available_at"]
        ) > datetime.fromisoformat(ready):
            raise SnapshotError("stockout_temporal_catalog_index_mismatch")
        return value["category_id"], value["source_record_sha256"]
