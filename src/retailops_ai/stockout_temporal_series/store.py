"""One complete replay of each parent into a private, capped physical-origin join."""

import hashlib
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    json_sha256,
    read_json,
)
from retailops_ai.stockout_label_partitions.bundle import (
    LabelPreparation,
)
from retailops_ai.stockout_label_partitions.bundle import (
    check_partition as check_label_partition,
)
from retailops_ai.stockout_label_partitions.bundle import (
    names as label_names,
)
from retailops_ai.stockout_label_partitions.bundle import (
    policies as label_policies,
)
from retailops_ai.stockout_label_partitions.store import LabelFacts
from retailops_ai.stockout_temporal_storage.store import (
    TemporalStore as FrozenTemporalStore,
)
from retailops_ai.stockout_temporal_storage.store import (
    capture,
)
from retailops_ai.stockout_upstream_series.bundle import (
    UpstreamPreparation,
    check_feature_parent,
)
from retailops_ai.stockout_upstream_series.bundle import (
    check_partition as check_upstream_partition,
)
from retailops_ai.stockout_upstream_series.bundle import (
    names as upstream_names,
)
from retailops_ai.stockout_upstream_series.bundle import (
    policies as upstream_policies,
)
from retailops_ai.stockout_upstream_series.store import SeriesFacts


class TemporalStore(FrozenTemporalStore):
    """Single-use internal context; no caller-supplied verified parents or cache.

    Pending rows are private until every part, manifest and source seal matches.
    Reuse frozen point queries and PIT catalog semantics; never expose a partial join.
    """

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
        self._load_upstream()
        self._load_labels()
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
        self._database_identity = self._identity()

    def _load_upstream(self) -> None:
        stored = read_json(self.inputs.upstream, "manifest.json")
        upstream_policy, partition_policy, storage_policy = upstream_policies(stored)
        # Features were already fully replayed within this context. Derive origins
        # from that private join, never from a caller's claimed verification result.
        origins = []
        for raw, checksum in self.connection().execute(
            "SELECT body,checksum FROM points WHERE kind='features' ORDER BY origin,product,stock"
        ):
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise SnapshotError("stockout_temporal_payload_checksum_mismatch")
            if len(raw) > self.policy.max_batch_bytes:
                raise SnapshotError("stockout_temporal_batch_resource_limit")
            point = decode_json(raw, limit=self.policy.max_batch_bytes)
            origins.append(
                (
                    point["product_id"],
                    point["stock_location_id"],
                    datetime.fromisoformat(point["as_of"].replace("Z", "+00:00")),
                    point["status"] == "eligible",
                )
            )
        with SeriesFacts(self.inputs.curated, policy=storage_policy, scratch=self.scratch) as facts:
            preparation = UpstreamPreparation(
                facts, self.parents["features"], origins, upstream_policy, partition_policy
            )
            for points, spec, blob in preparation.partitions():
                check_upstream_partition(self.inputs.upstream, spec, blob)
                for upstream_point in points:
                    self._insert("upstream", upstream_point.model_dump(mode="json"))
            expected = preparation.manifest()
        inventory(self.inputs.upstream, upstream_names(expected))
        check_feature_parent(self.inputs.features, self.parents["features"])
        if stored != expected:
            raise SnapshotError("stockout_upstream_manifest_full_replay_mismatch")
        self.parents["upstream"] = expected
        self.stats["upstream_replays"] = 1

    def _load_features(self) -> None:
        super()._load_features()
        self.stats["feature_replays"] = 1

    def _load_labels(self) -> None:
        stored = read_json(self.inputs.labels, "manifest.json")
        label_policy, partition_policy, storage_policy = label_policies(stored)
        with LabelFacts(
            self.inputs.private,
            label_policy,
            allow_evaluation_truth=self.allow_truth,
            storage_policy=storage_policy,
            scratch=self.scratch,
        ) as facts:
            preparation = LabelPreparation(facts, partition_policy)
            for points, spec, blob in preparation.partitions():
                check_label_partition(self.inputs.labels, spec, blob)
                for point in points:
                    self._insert("labels", point.model_dump(mode="json"))
            expected = preparation.manifest()
        inventory(self.inputs.labels, label_names(expected))
        if stored != expected:
            raise SnapshotError("stockout_label_manifest_full_replay_mismatch")
        self.parents["labels"] = expected
        self.stats["label_replays"] = 1

    def _identity(self) -> tuple[int, int, int, int, int]:
        info = self.path.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def check_database(self) -> None:
        db = self.connection()
        if (
            self.path.is_symlink()
            or self._identity() != self._database_identity
            or db.execute("PRAGMA query_only").fetchone() != (1,)
        ):
            raise SnapshotError("stockout_temporal_private_database_changed")
