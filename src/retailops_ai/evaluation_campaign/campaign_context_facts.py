"""Full, sealed AI09 context facts with the closed AI08 point projection.

This internal reader grants no exposure permission. Its caller must durably
reserve the whole source read and verify the completed generation/export first.
All versions stay on disk; each query retains original order and clips knowledge
before the unchanged AI08 projection. No simulation truth is accepted here.
"""

import hashlib
import sqlite3
import tempfile
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any, Self

from retailops_ai.curated.builder import iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, columns_for, decoded, encoded
from retailops_ai.data_contracts.common import utc_time
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignContextStoragePolicy,
    CampaignForecastContextScope,
    CampaignForecastSegmentPolicy,
    CampaignOriginRoute,
)
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_bytes,
)
from retailops_ai.source_snapshot.protocol import Limits
from retailops_ai.stockout.feature_contract import FeaturePoint
from retailops_ai.stockout.features import FEATURE_TABLES, effective, latest
from retailops_ai.stockout_history.projection import feature_point
from retailops_ai.stockout_storage.store import keys


class CampaignContextFacts:
    """One use, one sealed parent, bounded SQL queries, one cached origin point."""

    def __init__(
        self,
        curated: Path,
        *,
        scope: CampaignForecastContextScope,
        policy: CampaignContextStoragePolicy,
        scratch: Path,
    ) -> None:
        self.curated, self.scratch = curated, scratch
        self.scope = CampaignForecastContextScope.model_validate_json(scope.model_dump_json())
        self.policy = CampaignContextStoragePolicy.model_validate_json(policy.model_dump_json())
        self._stack = ExitStack()
        self._db: sqlite3.Connection | None = None
        self._used = self._failed = False
        self.document: dict[str, Any] = {}
        self.seal: dict[str, Any] = {}
        self.stats = {
            "stored_rows": 0,
            "payload_bytes": 0,
            "maximum_selected_rows": 0,
            "maximum_selected_bytes": 0,
            "maximum_index_bytes": 0,
            "origin_point_projections": 0,
        }
        self._point_key: tuple[str, str, datetime] | None = None
        self._point: FeaturePoint | None = None

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("campaign_context_facts_single_use")
        self._used = True
        try:
            self._open()
        except sqlite3.DatabaseError:
            self._failed = True
            self._stack.close()
            self._db = None
            raise SnapshotError(
                "campaign_context_facts_database_resource_or_write_failure"
            ) from None
        except BaseException:
            self._failed = True
            self._stack.close()
            self._db = None
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        try:
            if args[0] is None and not self._failed:
                self.check_parent()
        finally:
            self._stack.close()
            self._db = None
            self._point = self._point_key = None

    def _budget(self) -> None:
        size = sum(p.stat().st_size for p in self.path.parent.iterdir() if p.is_file())
        self.stats["maximum_index_bytes"] = max(self.stats["maximum_index_bytes"], size)
        if size > self.policy.max_index_bytes:
            raise SnapshotError("campaign_context_facts_combined_index_resource_limit")

    def _open(self) -> None:
        root, parent = checked_directory(self.curated), checked_directory(self.scratch)
        if root == parent or root in parent.parents:
            raise SnapshotError("campaign_context_facts_scratch_inside_parent")
        self._initial = read_bytes(root, "curated_manifest.json")
        limits = Limits(
            max_rows=self.policy.max_parent_rows,
            max_bytes=self.policy.max_parent_bytes,
            max_files=self.policy.max_parent_files,
            batch_rows=self.policy.batch_rows,
        )
        document = verify_curated(root, limits=limits)
        descriptor = document["descriptor"]
        if (
            decode_json(self._initial) != document
            or document["schema_version"] not in self.policy.supported_curated_versions
            or not document["readiness"]["inventory_ready"]
            or document["evaluation_truth"]["included"]
            or (
                document["curated_dataset_id"],
                descriptor["parent_source_dataset_id"],
                descriptor["parent_snapshot_id"],
                descriptor["source_parameters"]["seed"],
            )
            != (
                self.scope.curated_dataset_id,
                self.scope.source_dataset_id,
                self.scope.snapshot_id,
                self.scope.data_seed,
            )
        ):
            raise SnapshotError("campaign_context_facts_complete_parent_scope_mismatch")
        specs = {t["table"]: t for t in document["tables"]}
        if not set(FEATURE_TABLES) <= set(specs):
            raise SnapshotError("campaign_context_facts_missing_ai08_tables")
        self._refs = [f for t in [*document["tables"], document["quarantine"]] for f in t["files"]]
        if (
            len(self._refs) + 2 > self.policy.max_parent_files
            or sum(r["bytes"] for r in self._refs) + len(self._initial) + 65
            > self.policy.max_parent_bytes
        ):
            raise SnapshotError("campaign_context_facts_complete_parent_resource_limit")
        directory = Path(
            self._stack.enter_context(
                tempfile.TemporaryDirectory(prefix=".ai09-context-facts-", dir=parent)
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
        db.execute("PRAGMA max_page_count=" + str(self.policy.max_index_bytes // 4096))
        db.execute(
            "CREATE TABLE facts(table_name TEXT, position INTEGER, product TEXT, stock TEXT, ready TEXT, payload BLOB, checksum TEXT, PRIMARY KEY(table_name,position))"
        )
        db.execute("CREATE INDEX facts_lookup ON facts(table_name,product,stock,ready,position)")
        self._version = document["schema_version"]
        self._columns = {name: columns_for(name, self._version) for name in FEATURE_TABLES}
        summaries = []
        order = hashlib.sha256()
        try:
            for name in FEATURE_TABLES:
                spec = specs[name]
                path = directory / "table-seal.sqlite"
                digest = Digest(path, self._columns[name], spec["grain"])
                try:
                    for position, row in enumerate(
                        iter_rows(root, spec["files"], self.policy.batch_rows)
                    ):
                        digest.add(row)
                        raw = encoded(row)
                        product, stock, ready = keys(name, row)
                        checksum = hashlib.sha256(raw).hexdigest()
                        db.execute(
                            "INSERT INTO facts VALUES(?,?,?,?,?,?,?)",
                            (name, position, product, stock, ready, raw, checksum),
                        )
                        order.update(canonical_json([name, position, checksum]) + b"\n")
                        self.stats["stored_rows"] += 1
                        self.stats["payload_bytes"] += len(raw)
                        if self.stats["stored_rows"] > self.policy.max_parent_rows:
                            raise SnapshotError("campaign_context_facts_parent_row_resource_limit")
                        if self.stats["stored_rows"] % self.policy.batch_rows == 0:
                            db.commit()
                            self._budget()
                    summary = digest.summary()
                    if any(spec[k] != v for k, v in summary.items()):
                        raise SnapshotError("campaign_context_facts_parent_changed_during_read")
                    summaries.append({"table": name, **summary})
                    db.commit()
                    self._budget()
                finally:
                    digest.close()
                    path.unlink()
        except sqlite3.DatabaseError:
            raise SnapshotError(
                "campaign_context_facts_database_resource_or_write_failure"
            ) from None
        self._stored_rows = self.stats["stored_rows"]
        self._stored_trace = order.hexdigest()
        self.document = document
        self.check_parent()
        self.seal = {
            "context_scope_sha256": self.scope.content_sha256(),
            "storage_policy_sha256": self.policy.content_sha256(),
            "curated_manifest_sha256": hashlib.sha256(self._initial).hexdigest(),
            "complete_parent_files_sha256": canonical_sha256(self._refs),
            "tables": summaries,
            "ordered_rows_sha256": order.hexdigest(),
            "audited_source_read_proved_by_this_seal": False,
        }
        self._budget()
        db.execute("PRAGMA query_only=ON")

    def _connection(self) -> sqlite3.Connection:
        if self._db is None or not self.document or self._failed:
            raise SnapshotError("campaign_context_facts_closed_unsealed_or_failed")
        return self._db

    def check_parent(self) -> None:
        if self._failed:
            raise SnapshotError("campaign_context_facts_closed_unsealed_or_failed")
        try:
            inventory(
                self.curated,
                {"curated_manifest.json", "manifest.sha256"} | {r["path"] for r in self._refs},
            )
            if (
                read_bytes(self.curated, "curated_manifest.json") != self._initial
                or read_bytes(self.curated, "manifest.sha256", 128)
                != (hashlib.sha256(self._initial).hexdigest() + "\n").encode()
                or any(
                    file_hash(self.curated, r["path"]) != (r["bytes"], r["sha256"])
                    for r in self._refs
                )
            ):
                raise SnapshotError("campaign_context_facts_complete_parent_changed")
            self._check_index()
        except Exception:
            self._failed = True
            raise

    def _check_index(self) -> None:
        """Recheck all rows and lookup keys, including facts never queried."""
        db = self._connection()
        rows = 0
        trace = hashlib.sha256()
        for table in FEATURE_TABLES:
            position = 0
            for stored_position, product, stock, ready, raw, checksum in db.execute(
                "SELECT position,product,stock,ready,payload,checksum FROM facts WHERE table_name=? ORDER BY position",
                (table,),
            ):
                if (
                    stored_position != position
                    or hashlib.sha256(raw).hexdigest() != checksum
                    or keys(table, decoded(raw, self._columns[table])) != (product, stock, ready)
                ):
                    raise SnapshotError("campaign_context_facts_complete_private_index_mismatch")
                trace.update(canonical_json([table, position, checksum]) + b"\n")
                position += 1
                rows += 1
        if rows != self._stored_rows or trace.hexdigest() != self._stored_trace:
            raise SnapshotError("campaign_context_facts_complete_private_index_mismatch")

    def _selected(
        self,
        table: str,
        product: str | None,
        stock: str | None,
        origin: str,
        *,
        remaining_rows: int | None = None,
        remaining_bytes: int | None = None,
    ) -> list[tuple[int, dict[str, Any], int]]:
        db = self._connection()
        selected: list[tuple[int, dict[str, Any], int]] = []
        row_limit = self.policy.max_selected_rows if remaining_rows is None else remaining_rows
        byte_limit = self.policy.max_selected_bytes if remaining_bytes is None else remaining_bytes
        size = 0
        for position, ready, raw, checksum in db.execute(
            "SELECT position,ready,payload,checksum FROM facts WHERE table_name=? AND product IS ? AND stock IS ? AND ready<=? ORDER BY ready,position LIMIT ?",
            (table, product, stock, origin, row_limit + 1),
        ):
            size += len(raw)
            if len(selected) >= row_limit or size > byte_limit:
                raise SnapshotError("campaign_context_facts_selected_resource_limit")
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise SnapshotError("campaign_context_facts_private_row_checksum_mismatch")
            row = decoded(raw, self._columns[table])
            if keys(table, row) != (product, stock, ready):
                raise SnapshotError("campaign_context_facts_private_row_index_mismatch")
            selected.append((position, row, len(raw)))
        return selected

    def known(self, product: str, stock: str, as_of: datetime) -> dict[str, list[dict[str, Any]]]:
        try:
            origin = utc_time(as_of).isoformat(timespec="microseconds")
            result = {}
            count = size = 0
            for table in FEATURE_TABLES:
                selected = []
                for p, s in {(p, s) for p in (None, product) for s in (None, stock)}:
                    for position, row, length in self._selected(
                        table,
                        p,
                        s,
                        origin,
                        remaining_rows=self.policy.max_selected_rows - count,
                        remaining_bytes=self.policy.max_selected_bytes - size,
                    ):
                        count += 1
                        size += length
                        if (
                            count > self.policy.max_selected_rows
                            or size > self.policy.max_selected_bytes
                        ):
                            raise SnapshotError("campaign_context_facts_selected_resource_limit")
                        selected.append((position, row))
                result[table] = [r for _, r in sorted(selected, key=lambda r: r[0])]
            self.stats["maximum_selected_rows"] = max(self.stats["maximum_selected_rows"], count)
            self.stats["maximum_selected_bytes"] = max(self.stats["maximum_selected_bytes"], size)
            return result
        except Exception:
            self._failed = True
            raise

    def route(self, feature: InputRow) -> CampaignOriginRoute | None:
        try:
            feature = InputRow.model_validate_json(feature.model_dump_json())
            rows = [
                r
                for _, r, _ in self._selected(
                    "fulfillment_routes",
                    None,
                    None,
                    feature.forecast_origin.isoformat(timespec="microseconds"),
                )
            ]
            routes = [
                r
                for r in latest(effective(rows, feature.forecast_origin.date()), ("route_key",))
                if (r["selling_location_id"], r["channel"])
                == (feature.selling_location_id, feature.channel)
            ]
            if len(routes) > 1:
                raise SnapshotError("campaign_context_facts_ambiguous_origin_route")
            if not routes:
                return None
            r = routes[0]
            return CampaignOriginRoute(
                product_id=feature.product_id,
                selling_location_id=feature.selling_location_id,
                channel=feature.channel,
                stock_location_id=r["stock_location_id"],
                source_record_sha256=r["source_record_sha256"],
                available_at=r["curated_available_at"],
                effective_from=r["effective_from"],
                effective_to=r["effective_to"],
            )
        except Exception:
            self._failed = True
            raise

    def point(self, feature: InputRow, route: CampaignOriginRoute | None) -> FeaturePoint | None:
        try:
            actual_route = self.route(feature)
            if actual_route != route:
                raise SnapshotError("campaign_context_facts_declared_route_does_not_match_source")
            if route is None:
                return None
            key = feature.product_id, route.stock_location_id, feature.forecast_origin
            if self._point_key != key:
                self._point = self._point_key = None
                self._point = feature_point(
                    self.known(*key), product=key[0], stock=key[1], as_of=key[2]
                )
                self._point_key = key
                self.stats["origin_point_projections"] += 1
            self._connection()
            return self._point
        except Exception:
            self._failed = True
            raise

    def check_categories(self, policy: CampaignForecastSegmentPolicy) -> None:
        db = self._connection()
        categories = set()
        for product, stock, ready, raw, checksum in db.execute(
            "SELECT product,stock,ready,payload,checksum FROM facts WHERE table_name='product_catalog' ORDER BY position"
        ):
            if hashlib.sha256(raw).hexdigest() != checksum:
                self._failed = True
                raise SnapshotError("campaign_context_facts_private_row_checksum_mismatch")
            row = decoded(raw, self._columns["product_catalog"])
            if keys("product_catalog", row) != (product, stock, ready):
                self._failed = True
                raise SnapshotError("campaign_context_facts_private_row_index_mismatch")
            categories.add(row["category_id"])
            if len(categories) > 256:
                self._failed = True
                raise SnapshotError("campaign_context_facts_category_inventory_resource_limit")
        if (
            tuple(sorted(categories)) != policy.category_inventory
            or policy.content_sha256() != self.scope.segment_policy_sha256
        ):
            self._failed = True
            raise SnapshotError("campaign_context_facts_complete_category_inventory_mismatch")
