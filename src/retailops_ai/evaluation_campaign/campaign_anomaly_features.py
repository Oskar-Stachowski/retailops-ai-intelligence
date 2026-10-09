"""Whole causal native Point census using bounded disk context groups.

Every public context row is verified before projection. Native clocks, history,
version selection and ambiguity handling remain the original Features methods.
This primitive does not authorize outcome reads or qualify a Project campaign.
"""

import hashlib
import sqlite3
import tempfile
import zlib
from collections.abc import Iterator
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta
from itertools import zip_longest
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import Field

if TYPE_CHECKING:
    from retailops_ai.evaluation_campaign.campaign_anomaly_membership import (
        AnomalyMembershipRow,
        CampaignAnomalyMembershipPlan,
    )

from retailops_ai.anomaly_detectors.protocol import Scope, Window, series_key
from retailops_ai.curated.builder import iter_rows
from retailops_ai.curated.contract import Digest, columns_for, decoded, encoded
from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.day_qualification.contract import GRAIN, Day
from retailops_ai.evaluation_campaign.campaign_anomaly_day_gate import CampaignAnomalyDiskDayGate
from retailops_ai.evaluation_campaign.campaign_anomaly_scoring import (
    HISTORY_DAYS,
    MAX_REQUESTED_ROWS,
    MAX_SCOPES,
)
from retailops_ai.evaluation_campaign.source_replay import physical_limits
from retailops_ai.qualified_anomalies.contract import MAX_BYTES, Context, Point, Policy
from retailops_ai.qualified_anomalies.features import TABLES, Features
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, checked_directory

Row = dict[str, Any]
ORDER = "event_type,product_id,location_id,channel,currency,business_date"


class CampaignAnomalyFeatureStateError(RuntimeError):
    """Private state failure must never become operational input quarantine."""


class CampaignAnomalyFeaturePlan(Contract):
    version: Literal["ai09-native-disk-anomaly-features-plan-1.0.0"] = (
        "ai09-native-disk-anomaly-features-plan-1.0.0"
    )
    day_gate_plan_sha256: Sha256
    native_days_sha256: Sha256
    policy: Policy = Field(default_factory=Policy)
    expected_points: Annotated[int, Field(ge=1, le=20000000)]
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)]
    max_context_rows: Annotated[int, Field(ge=1, le=1000000)] = 100000
    max_context_bytes: Annotated[int, Field(ge=4096, le=256 * 1024**2)] = 64 * 1024**2
    # A single valid native Point can contain large missing-fact lists. Keep
    # the native artifact byte bound (including its newline), never truncate it.
    max_point_bytes: Annotated[int, Field(ge=4096, lt=MAX_BYTES)] = MAX_BYTES - 1
    sqlite_cache_kib: Literal[4096] = 4096
    population: Literal["all_declared_native_sales_and_returns_days"] = (
        "all_declared_native_sales_and_returns_days"
    )


class _NativeFeatures(Features):
    def __init__(self, owner: "CampaignAnomalyFeatureProjection") -> None:
        # The original constructor copies all tables. Only context groups are
        # copied below; the original point/history/statistics kernel is unchanged.
        self.owner = owner
        self.gate = owner.gate
        self.policy = owner.plan.policy

    def context(self, day: Day, fit: datetime, scoring: datetime) -> Context:
        return Features(self.gate, self.owner._context_tables(day), self.policy).context(
            day, fit, scoring
        )


class CampaignAnomalyFeatureProjection:
    """Produce all native points and expose their complete ordered disk stream.

    Context selection keeps all product plan versions before native latest(),
    all date/series mapping versions and all product/date inventory versions.
    Counts/bytes are hard budgets: exceeding them rejects the entire projection.
    All enclosing gate, days, replay and public Source contexts must finish too.
    """

    def __init__(
        self, gate: CampaignAnomalyDiskDayGate, plan: CampaignAnomalyFeaturePlan, scratch: Path
    ) -> None:
        self.gate, self.scratch = gate, scratch
        self.plan = CampaignAnomalyFeaturePlan.model_validate_json(plan.model_dump_json())
        self._stack = ExitStack()
        self._database: sqlite3.Connection | None = None
        self._used = self._failed = self._complete = self._loaded = False
        self.stats = {
            "stored_context_rows": 0,
            "projected_points": 0,
            "maximum_context_rows": 0,
            "maximum_context_bytes": 0,
            "maximum_point_bytes": 0,
            "maximum_compressed_point_bytes": 0,
            "point_payload_bytes": 0,
            "compressed_point_payload_bytes": 0,
            "maximum_index_bytes": 0,
            "retained_index_bytes": 0,
        }

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("campaign_anomaly_features_single_use")
        self._used = True
        try:
            self._open()
        except BaseException:
            self._failed = True
            self._stack.close()
            self._database = None
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        try:
            if args[0] is None:
                self._budget()
                if self._hash_points() != self.native_points_sha256:
                    raise CampaignAnomalyFeatureStateError(
                        "campaign_anomaly_features_private_census_changed"
                    )
                self.gate.replay.verify_outputs()
                if self.gate.projection._hash_days() != self.plan.native_days_sha256:
                    raise CampaignAnomalyFeatureStateError(
                        "campaign_anomaly_features_private_days_changed"
                    )
                self.gate.projection.parent._replay.check_parents()
                self._receipt = {
                    "version": "ai09-native-disk-anomaly-features-receipt-1.0.0",
                    "plan_sha256": canonical_sha256(self.plan.model_dump(mode="json")),
                    "plan": self.plan.model_dump(mode="json"),
                    "native_points_sha256": self.native_points_sha256,
                    "point_index_sha256": self._index_sha256,
                    "point_storage": "zlib_level_1_native_json_bounded_decompression",
                    "context_table_traces": deepcopy(self._context_traces),
                    "stats": deepcopy(self.stats),
                    "complete_native_point_census_passed": True,
                    "outer_gate_days_replay_public_parent_completion_required": True,
                    "source_generation_ancestry_verified": False,
                    "producer_closure_policy_verified": False,
                    "audited_read_authorization_proven": False,
                    "business_event_day_completeness": "not_qualified",
                    "quality_qualified": False,
                    "stage_ready": False,
                }
                self._complete = True
        except BaseException:
            self._failed = True
            raise
        finally:
            try:
                self._stack.__exit__(*args)
            except BaseException:
                self._failed = True
                raise
            finally:
                self._database = None

    def receipt(self) -> Row:
        if not self._complete or self._failed:
            raise SnapshotError("campaign_anomaly_features_not_completed")
        return deepcopy(self._receipt)

    def _db(self) -> sqlite3.Connection:
        self.gate._live()
        if self._database is None or self._failed:
            raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_state_unavailable")
        return self._database

    def _budget(self, digest: Digest | None = None, digest_path: Path | None = None) -> None:
        sizes = {
            path: max(info.st_size, info.st_blocks * 512)
            for path in self.path.parent.iterdir()
            if path.is_file()
            for info in (path.stat(),)
        }
        databases = [(self.path, self._db())]
        if digest is not None and digest_path is not None:
            databases.append((digest_path, digest.db))
        for path, database in databases:
            size = (
                database.execute("PRAGMA page_count").fetchone()[0]
                * database.execute("PRAGMA page_size").fetchone()[0]
            )
            sizes[path] = max(size, sizes.get(path, 0))
        size = sum(sizes.values())
        self.stats["maximum_index_bytes"] = max(self.stats["maximum_index_bytes"], size)
        if size > self.plan.max_index_bytes:
            raise SnapshotError("campaign_anomaly_features_combined_index_budget")

    @staticmethod
    def _header(row: Row) -> tuple[Any, ...]:
        return (
            row["product_id"],
            row.get("selling_location_id"),
            row.get("channel"),
            row["business_date"].isoformat() if row.get("business_date") is not None else None,
        )

    def _checked_context(self, name: str, values: tuple[Any, ...]) -> Row:
        *header, raw, digest = values
        if hashlib.sha256(raw).hexdigest() != digest:
            raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_private_context_row")
        row = decoded(raw, self._columns[name])
        if encoded(row) != raw or self._header(row) != tuple(header):
            raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_private_context_key")
        return row

    def _verify_context(self) -> None:
        for name in TABLES:
            trace = hashlib.sha256()
            count = 0
            for position, *values in self._db().execute(
                "SELECT position,product_id,location_id,channel,business_date,payload,digest "
                "FROM context WHERE name=? ORDER BY position",
                (name,),
            ):
                if position != count:
                    raise CampaignAnomalyFeatureStateError(
                        "campaign_anomaly_features_private_context_position"
                    )
                self._checked_context(name, tuple(values))
                trace.update(values[-2] + b"\n")
                count += 1
            if {"rows": count, "sha256": trace.hexdigest()} != self._context_traces[name]:
                raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_private_context")

    def _context_tables(self, day: Day) -> dict[str, list[Row]]:
        tables: dict[str, list[Row]] = {name: [] for name in TABLES}
        count = size = 0
        for name in TABLES:
            where = "name=? AND product_id=?"
            params: tuple[Any, ...] = (name, day.product_id)
            if name == "daily_demand_versions":
                # Currency is deliberately excluded, matching the native mapping.
                where += " AND location_id=? AND channel=? AND business_date=?"
                params += (day.selling_location_id, day.channel, day.business_date)
            elif name == "inventory_daily_snapshots":
                where += " AND business_date=?"
                params += (day.business_date,)
            for values in self._db().execute(
                "SELECT product_id,location_id,channel,business_date,payload,digest "
                "FROM context WHERE " + where + " ORDER BY position",
                params,
            ):
                count += 1
                size += len(values[-2])
                if count > self.plan.max_context_rows or size > self.plan.max_context_bytes:
                    raise SnapshotError("campaign_anomaly_features_complete_context_budget")
                tables[name].append(self._checked_context(name, values))
        self.stats["maximum_context_rows"] = max(self.stats["maximum_context_rows"], count)
        self.stats["maximum_context_bytes"] = max(self.stats["maximum_context_bytes"], size)
        return tables

    def _point_raw(self, payload: bytes, digest: str) -> bytes:
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(payload, self.plan.max_point_bytes + 1)
            if (
                len(raw) > self.plan.max_point_bytes
                or not decoder.eof
                or decoder.unconsumed_tail
                or decoder.unused_data
            ):
                raise ValueError("point_compression_or_size")
        except (ValueError, zlib.error):
            raise CampaignAnomalyFeatureStateError(
                "campaign_anomaly_features_private_point_compression"
            ) from None
        if hashlib.sha256(raw).hexdigest() != digest:
            raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_private_point_row")
        return raw

    def _checked_point(self, values: tuple[Any, ...]) -> Point:
        *grain, payload, digest = values
        raw = self._point_raw(payload, digest)
        point = Point.model_validate_json(raw)
        key = tuple(
            point.business_date.isoformat() if k == "business_date" else getattr(point, k)
            for k in GRAIN
        )
        if key != tuple(grain):
            raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_private_point_key")
        return point

    def points(self) -> Iterator[Point]:
        self._db()
        if not self._loaded:
            raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_points_unavailable")
        for values in self._db().execute(
            "SELECT event_type,business_date,product_id,location_id,channel,currency,payload,digest "
            "FROM points ORDER BY " + ORDER
        ):
            self._db()
            yield self._checked_point(values)

    def scoring_points(self, scopes: tuple[Scope, ...], window: Window) -> Iterator[Point]:
        """Read a native scoring window and its six preceding feature days.

        Keep every requested series, including series with no declared points:
        the scorer emits their native abstentions. The existing index retrieves
        one series at a time, without collecting its rows or decompressing the
        rest of the parent. Completion still verifies the entire stored census;
        a selected window cannot qualify a corrupted unselected parent.
        This method grants no campaign, outcome or final access permission.
        """
        self._db()
        if not self._loaded:
            raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_points_unavailable")
        if not 1 <= len(scopes) <= MAX_SCOPES:
            raise ValueError("campaign_anomaly_features_scoring_scope_budget")
        window = Window.model_validate_json(window.model_dump_json())
        scopes = tuple(Scope.model_validate_json(s.model_dump_json()) for s in scopes)
        keys = [series_key(s) for s in scopes]
        days = (window.end - window.start).days + 1
        if keys != sorted(set(keys)) or days > 2001 or len(keys) * days > MAX_REQUESTED_ROWS:
            raise ValueError("campaign_anomaly_features_scoring_scope_or_window")
        earliest = window.start - timedelta(days=HISTORY_DAYS)
        for key in keys:
            for values in self._db().execute(
                "SELECT event_type,business_date,product_id,location_id,channel,currency,payload,digest "
                "FROM points WHERE event_type=? AND product_id=? AND location_id=? "
                "AND channel=? AND currency=? AND business_date BETWEEN ? AND ? "
                "ORDER BY business_date",
                (*key, earliest.isoformat(), window.end.isoformat()),
            ):
                self._db()
                yield self._checked_point(values)

    def training_memberships(
        self, plan: "CampaignAnomalyMembershipPlan"
    ) -> Iterator["AnomalyMembershipRow"]:
        """Bind the full live native feature parent before producing causal rows."""
        from retailops_ai.evaluation_campaign.campaign_anomaly_membership import (
            CampaignAnomalyMembershipPlan,
            iter_anomaly_membership_census,
        )

        self._db()
        plan = CampaignAnomalyMembershipPlan.model_validate_json(plan.model_dump_json())
        if (
            plan.feature_plan_sha256 != canonical_sha256(self.plan.model_dump(mode="json"))
            or plan.native_points_sha256 != self.native_points_sha256
            or self.plan.policy != Policy()
        ):
            raise ValueError("campaign_anomaly_membership_complete_feature_parent_binding")
        actual_scopes = self._db().execute(
            "SELECT DISTINCT event_type,product_id,location_id,channel,currency "
            "FROM points ORDER BY event_type,product_id,location_id,channel,currency"
        )
        if any(
            expected != actual
            for expected, actual in zip_longest((series_key(s) for s in plan.scopes), actual_scopes)
        ):
            raise ValueError("campaign_anomaly_membership_complete_scope_inventory")
        yield from iter_anomaly_membership_census(
            self.scoring_points(plan.scopes, Window(start=plan.train.start, end=plan.test.end)),
            plan,
        )

    def _hash_points(self) -> str:
        trace = hashlib.sha256()
        index = hashlib.sha256()
        count = 0
        for values in self._db().execute(
            "SELECT event_type,business_date,product_id,location_id,channel,currency,payload,digest "
            "FROM points ORDER BY " + ORDER
        ):
            # The fixed write-time traces authenticate the exact original native
            # bytes and every lookup header. Revalidating all nested Point
            # objects here would repeatedly allocate the same complete history.
            raw = self._point_raw(values[-2], values[-1])
            trace.update(raw + b"\n")
            index.update(canonical_json(values[:-2]) + b"\n" + raw + b"\n")
            count += 1
        if count != self.plan.expected_points:
            raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_private_point_extent")
        if index.hexdigest() != self._index_sha256:
            raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_private_point_index")
        return trace.hexdigest()

    def _open(self) -> None:
        self.gate._live()
        if (
            canonical_sha256(self.gate.plan.model_dump(mode="json"))
            != self.plan.day_gate_plan_sha256
            or self.gate.projection.native_days_sha256 != self.plan.native_days_sha256
            or len(self.gate.days) != self.plan.expected_points
        ):
            raise SnapshotError("campaign_anomaly_features_complete_parent_binding")
        parent = self.gate.projection.parent
        scratch = checked_directory(self.scratch)
        for original in (parent.snapshot.resolve(), parent.curated.resolve()):
            if scratch == original or original in scratch.parents:
                raise SnapshotError("campaign_anomaly_features_scratch_inside_source")
        parent._replay.check_parents()
        directory = Path(
            self._stack.enter_context(
                tempfile.TemporaryDirectory(prefix=".ai09-anomaly-features-", dir=scratch)
            )
        )
        self.path = directory / "features.sqlite"
        self.path.touch(mode=0o600, exist_ok=False)
        database = sqlite3.connect(self.path)
        self._database = database
        self._stack.callback(database.close)
        database.execute("PRAGMA cache_size=-4096")
        database.execute("PRAGMA mmap_size=0")
        database.execute("PRAGMA temp_store=FILE")
        database.execute("PRAGMA auto_vacuum=FULL")
        database.executescript(
            "CREATE TABLE context(name TEXT,position INTEGER,product_id TEXT,location_id TEXT,channel TEXT,business_date TEXT,payload BLOB,digest TEXT,PRIMARY KEY(name,position));"
            "CREATE INDEX context_lookup ON context(name,product_id,business_date,location_id,channel,position);"
            "CREATE TABLE points(event_type TEXT,business_date TEXT,product_id TEXT,location_id TEXT,channel TEXT,currency TEXT,payload BLOB,digest TEXT,PRIMARY KEY(event_type,business_date,product_id,location_id,channel,currency));"
            "CREATE INDEX point_order ON points(event_type,product_id,location_id,channel,currency,business_date);"
        )
        specs = {row["table"]: row for row in parent._replay.manifest["tables"]}
        if not set(TABLES) <= set(specs):
            raise SnapshotError("campaign_anomaly_features_context_allowlist")
        self._columns = {
            name: columns_for(name, parent.plan.source.schema_version) for name in TABLES
        }
        self._context_traces: dict[str, Row] = {}
        limits = physical_limits(parent.plan.source)
        self._budget()
        for name in TABLES:
            spec = specs[name]
            path = directory / "digest.sqlite"
            digest = Digest(path, self._columns[name], spec["grain"])
            trace = hashlib.sha256()
            count = 0
            try:
                digest.db.execute("CREATE INDEX canonical_sort ON rows(record)")
                for position, row in enumerate(
                    iter_rows(parent._replay.curated, spec["files"], limits.batch_rows)
                ):
                    digest.add(row)
                    raw = encoded(row)
                    database.execute(
                        "INSERT INTO context VALUES(?,?,?,?,?,?,?,?)",
                        (name, position, *self._header(row), raw, hashlib.sha256(raw).hexdigest()),
                    )
                    trace.update(raw + b"\n")
                    count += 1
                    self.stats["stored_context_rows"] += 1
                    if count % limits.batch_rows == 0:
                        self._budget(digest, path)
                        database.commit()
                        digest.db.commit()
                        self._budget(digest, path)
                if any(spec[key] != value for key, value in digest.summary().items()):
                    raise SnapshotError("campaign_anomaly_features_context_changed_during_load")
                self._context_traces[name] = {"rows": count, "sha256": trace.hexdigest()}
                self._budget(digest, path)
                database.commit()
                self._budget(digest, path)
            finally:
                digest.close()
                path.unlink(missing_ok=True)
        self._verify_context()
        native = _NativeFeatures(self)
        trace = hashlib.sha256()
        index = hashlib.sha256()
        for grain in self.gate.projection._db().execute(
            "SELECT event_type,business_date,product_id,location_id,channel,currency "
            "FROM days ORDER BY " + ORDER
        ):
            day = self.gate.days[tuple(grain)]
            point = native.point(day)
            raw = canonical_json(point.model_dump(mode="json"))
            if len(raw) > self.plan.max_point_bytes:
                raise SnapshotError("campaign_anomaly_features_point_budget")
            self.stats["maximum_point_bytes"] = max(self.stats["maximum_point_bytes"], len(raw))
            compressed = zlib.compress(raw, level=1)
            self.stats["maximum_compressed_point_bytes"] = max(
                self.stats["maximum_compressed_point_bytes"], len(compressed)
            )
            self.stats["point_payload_bytes"] += len(raw)
            self.stats["compressed_point_payload_bytes"] += len(compressed)
            database.execute(
                "INSERT INTO points VALUES(?,?,?,?,?,?,?,?)",
                (*(getattr(day, k) for k in GRAIN), compressed, hashlib.sha256(raw).hexdigest()),
            )
            trace.update(raw + b"\n")
            index.update(canonical_json(grain) + b"\n" + raw + b"\n")
            self.stats["projected_points"] += 1
            if self.stats["projected_points"] % limits.batch_rows == 0:
                self._budget()
                database.commit()
                self._budget()
        self.native_points_sha256 = trace.hexdigest()
        self._index_sha256 = index.hexdigest()
        if self._hash_points() != self.native_points_sha256:
            raise CampaignAnomalyFeatureStateError("campaign_anomaly_features_private_point_census")
        self._verify_context()
        parent._replay.check_parents()
        # All native points retain their historical context/references. Release
        # only the construction index; auto-vacuum keeps every output point.
        database.execute("DROP TABLE context")
        self._budget()
        database.commit()
        self._budget()
        self.stats["retained_index_bytes"] = max(
            self.path.stat().st_size, self.path.stat().st_blocks * 512
        )
        if self._hash_points() != self.native_points_sha256:
            raise CampaignAnomalyFeatureStateError(
                "campaign_anomaly_features_retention_changed_points"
            )
        self._loaded = True
