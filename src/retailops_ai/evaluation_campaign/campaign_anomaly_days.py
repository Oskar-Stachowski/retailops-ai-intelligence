"""Complete disk-backed day projection through unchanged native series semantics.

This reconstructs assertions from the whole sealed public Source. It does not
substitute for a producer closure policy, generation ancestry, audited reads,
causal raw-DQ qualification, feature construction or scientific qualification.
"""

import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field

from retailops_ai.curated.builder import iter_rows
from retailops_ai.curated.contract import Digest, columns_for, decoded, encoded
from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.day_qualification.contract import GRAIN, KEY, TABLES, Day
from retailops_ai.day_qualification.projection import declarations
from retailops_ai.evaluation_campaign.campaign_anomaly_parent import (
    CampaignAnomalyParentStateError,
    CampaignAnomalyPublicParent,
)
from retailops_ai.evaluation_campaign.source_replay import physical_limits
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, checked_directory

Row = dict[str, Any]
Grain = tuple[str, ...]


class CampaignAnomalyDayPlan(Contract):
    version: Literal["ai09-anomaly-full-day-projection-plan-1.0.0"] = (
        "ai09-anomaly-full-day-projection-plan-1.0.0"
    )
    parent_plan_sha256: Sha256
    parent_events_sha256: Sha256
    expected_days: Annotated[int, Field(ge=1, le=20000000)]
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)]
    max_series_rows: Annotated[int, Field(ge=1, le=1000000)] = 100000
    max_series_bytes: Annotated[int, Field(ge=4096, le=256 * 1024**2)] = 64 * 1024**2
    max_series: Annotated[int, Field(ge=1, le=65536)] = 65536
    max_record_bytes: Annotated[int, Field(ge=1024, le=65536)] = 65536
    sqlite_cache_kib: Literal[4096] = 4096
    population: Literal["all_native_sales_days_and_parent_purchase_return_tail"] = (
        "all_native_sales_days_and_parent_purchase_return_tail"
    )


class CampaignAnomalyDayDiscoveryPlan(CampaignAnomalyDayPlan):
    """Resolve the exact census from all native declarations within a frozen cap."""

    version: Literal["ai09-anomaly-full-day-discovery-plan-1.0.0"] = (
        "ai09-anomaly-full-day-discovery-plan-1.0.0"  # type: ignore[assignment]
    )
    expected_days: None = None  # type: ignore[assignment]
    max_days: Annotated[int, Field(ge=1, le=20000000)] = 20000000


class _Days(Mapping[Grain, Day]):
    def __init__(self, owner: "CampaignAnomalyDayProjection") -> None:
        self.owner = owner

    def __len__(self) -> int:
        self.owner._db()
        return self.owner._resolved_day_count

    def __iter__(self) -> Iterator[Grain]:
        for row in self.owner._db().execute(
            "SELECT event_type,business_date,product_id,location_id,channel,currency FROM days "
            "ORDER BY event_type,business_date,product_id,location_id,channel,currency"
        ):
            self.owner._db()
            yield tuple(row)

    def __getitem__(self, grain: Grain) -> Day:
        if len(grain) != len(GRAIN):
            raise KeyError(grain)
        row = (
            self.owner._db()
            .execute(
                "SELECT payload,digest FROM days WHERE event_type=? AND business_date=? "
                "AND product_id=? AND location_id=? AND channel=? AND currency=?",
                grain,
            )
            .fetchone()
        )
        if row is None:
            raise KeyError(grain)
        day = self.owner._checked(row[0], row[1])
        if tuple(getattr(day, key) for key in GRAIN) != grain:
            raise CampaignAnomalyParentStateError("campaign_anomaly_day_private_grain_changed")
        return day


class CampaignAnomalyDayProjection:
    """Project every complete independent series, retain every native day on disk.

    The enclosing public-parent context must finish its final verification
    successfully before any encompassing artifact can be accepted. This inner
    receipt deliberately does not claim that the outer context has completed.
    Native limits remain applicable to each complete series and every Day.
    """

    def __init__(
        self,
        parent: CampaignAnomalyPublicParent,
        plan: CampaignAnomalyDayPlan | CampaignAnomalyDayDiscoveryPlan,
        scratch: Path,
    ) -> None:
        self.parent, self.scratch = parent, scratch
        plan_type = (
            CampaignAnomalyDayDiscoveryPlan
            if isinstance(plan, CampaignAnomalyDayDiscoveryPlan)
            else CampaignAnomalyDayPlan
        )
        self.plan = plan_type.model_validate_json(plan.model_dump_json())
        self._day_limit = (
            self.plan.max_days
            if isinstance(self.plan, CampaignAnomalyDayDiscoveryPlan)
            else self.plan.expected_days
        )
        self._resolved_day_count = 0
        self._stack = ExitStack()
        self._database: sqlite3.Connection | None = None
        self._used = self._failed = self._complete = False
        self.stats = {
            "stored_source_rows": 0,
            "projected_days": 0,
            "series": 0,
            "maximum_series_rows": 0,
            "maximum_series_bytes": 0,
            "maximum_index_bytes": 0,
            "retained_index_bytes": 0,
        }

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("campaign_anomaly_day_single_use")
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
                self.parent._replay.check_parents()
                self._budget()
                if self._hash_days() != self.native_days_sha256:
                    raise CampaignAnomalyParentStateError(
                        "campaign_anomaly_day_private_index_changed"
                    )
                self._receipt = {
                    "version": "ai09-anomaly-full-day-projection-receipt-1.0.0",
                    "plan_sha256": canonical_sha256(self.plan.model_dump(mode="json")),
                    "parent_plan_sha256": self.plan.parent_plan_sha256,
                    "parent_events_sha256": self.plan.parent_events_sha256,
                    "source_dataset_id": self.parent.plan.source.parent.source_dataset_id,
                    "native_days_sha256": self.native_days_sha256,
                    "stats": deepcopy(self.stats),
                    "complete_native_day_projection_passed": True,
                    "public_parent_final_verification_required": True,
                    "producer_closure_policy_verified": False,
                    "source_generation_ancestry_verified": False,
                    "audited_read_authorization_proven": False,
                    "business_event_day_completeness": "not_qualified",
                    "quality_qualified": False,
                    "stage_ready": False,
                }
                if isinstance(self.plan, CampaignAnomalyDayDiscoveryPlan):
                    self._receipt.update(
                        version="ai09-anomaly-full-day-discovery-receipt-1.0.0",
                        resolved_day_count=self._resolved_day_count,
                        population_reduced=False,
                    )
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
            raise SnapshotError("campaign_anomaly_day_not_completed")
        return deepcopy(self._receipt)

    def _db(self) -> sqlite3.Connection:
        # A day index must not outlive its verified parent context.
        self.parent._db()
        if self._database is None or self._failed:
            raise CampaignAnomalyParentStateError("campaign_anomaly_day_state_unavailable")
        return self._database

    def _checked(self, raw: bytes, digest: str) -> Day:
        if hashlib.sha256(raw).hexdigest() != digest:
            raise CampaignAnomalyParentStateError("campaign_anomaly_day_private_row_changed")
        return Day.model_validate_json(raw)

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
            logical = (
                database.execute("PRAGMA page_count").fetchone()[0]
                * database.execute("PRAGMA page_size").fetchone()[0]
            )
            sizes[path] = max(sizes.get(path, 0), logical)
        size = sum(sizes.values())
        self.stats["maximum_index_bytes"] = max(self.stats["maximum_index_bytes"], size)
        if size > self.plan.max_index_bytes:
            raise SnapshotError("campaign_anomaly_day_combined_index_budget")

    def _open(self) -> None:
        self.parent._db()
        if (
            not self.parent._loaded
            or canonical_sha256(self.parent.plan.model_dump(mode="json"))
            != self.plan.parent_plan_sha256
            or self.parent.native_events_sha256 != self.plan.parent_events_sha256
        ):
            raise SnapshotError("campaign_anomaly_day_parent_binding")
        scratch = checked_directory(self.scratch)
        for original in (self.parent.snapshot.resolve(), self.parent.curated.resolve()):
            if scratch == original or original in scratch.parents:
                raise SnapshotError("campaign_anomaly_day_scratch_inside_source")
        self.parent._replay.check_parents()
        directory = Path(
            self._stack.enter_context(
                tempfile.TemporaryDirectory(prefix=".ai09-anomaly-days-", dir=scratch)
            )
        )
        self.path = directory / "days.sqlite"
        self.path.touch(mode=0o600, exist_ok=False)
        database = sqlite3.connect(self.path)
        self._database = database
        self._stack.callback(database.close)
        database.execute("PRAGMA cache_size=-4096")
        database.execute("PRAGMA mmap_size=0")
        database.execute("PRAGMA temp_store=FILE")
        database.execute("PRAGMA auto_vacuum=FULL")
        database.executescript(
            "CREATE TABLE rows(name TEXT,position INTEGER,series BLOB,natural TEXT,category TEXT,channel TEXT,payload BLOB,PRIMARY KEY(name,position));"
            "CREATE INDEX series_rows ON rows(name,series,position);"
            "CREATE INDEX natural_rows ON rows(name,natural,position);"
            "CREATE INDEX rule_rows ON rows(name,category,channel,position);"
            "CREATE TABLE series(value BLOB PRIMARY KEY);"
            "CREATE TABLE days(event_type TEXT,business_date TEXT,product_id TEXT,location_id TEXT,channel TEXT,currency TEXT,payload BLOB,digest TEXT,PRIMARY KEY(event_type,business_date,product_id,location_id,channel,currency));"
        )
        manifest = self.parent._replay.manifest
        specs = {row["table"]: row for row in manifest["tables"]}
        if not set(TABLES) <= set(specs):
            raise SnapshotError("campaign_anomaly_day_source_table_allowlist")
        version = self.parent.plan.source.schema_version
        self._columns = {name: columns_for(name, version) for name in TABLES}
        limits = physical_limits(self.parent.plan.source)
        self._budget()
        for name in TABLES:
            spec = specs[name]
            path = directory / "digest.sqlite"
            digest = Digest(path, self._columns[name], spec["grain"])
            try:
                digest.db.execute("CREATE INDEX canonical_sort ON rows(record)")
                for position, row in enumerate(
                    iter_rows(self.parent._replay.curated, spec["files"], limits.batch_rows)
                ):
                    raw = encoded(row)
                    if len(raw) > self.plan.max_record_bytes:
                        raise SnapshotError("campaign_anomaly_day_source_record_budget")
                    digest.add(row)
                    series_blob = (
                        canonical_json(tuple(row[k] for k in KEY))
                        if name in {"inventory_sales", "return_events", "daily_demand_observations"}
                        else None
                    )
                    database.execute(
                        "INSERT INTO rows VALUES(?,?,?,?,?,?,?)",
                        (
                            name,
                            position,
                            series_blob,
                            row.get("id"),
                            row.get("category_id"),
                            row.get("channel"),
                            raw,
                        ),
                    )
                    if name in {"inventory_sales", "daily_demand_observations"}:
                        database.execute("INSERT OR IGNORE INTO series VALUES(?)", (series_blob,))
                    self.stats["stored_source_rows"] += 1
                    if (position + 1) % limits.batch_rows == 0:
                        self._budget(digest, path)
                        database.commit()
                        digest.db.commit()
                        self._budget(digest, path)
                if any(spec[key] != value for key, value in digest.summary().items()):
                    raise SnapshotError("campaign_anomaly_day_table_changed_during_load")
                self._budget(digest, path)
                database.commit()
                self._budget(digest, path)
            finally:
                digest.close()
                path.unlink(missing_ok=True)
        self.stats["series"] = database.execute("SELECT COUNT(*) FROM series").fetchone()[0]
        if self.stats["series"] > self.plan.max_series:
            raise SnapshotError("campaign_anomaly_day_series_population_budget")
        start = manifest["descriptor"]["source_parameters"]["start_date"]
        for (series_raw,) in database.execute("SELECT value FROM series ORDER BY value"):
            series_key = tuple(json.loads(series_raw))
            tables = self._series_tables(series_key, series_raw)
            for day in declarations(tables, start):
                raw = canonical_json(day.model_dump(mode="json"))
                database.execute(
                    "INSERT INTO days VALUES(?,?,?,?,?,?,?,?)",
                    (*(getattr(day, key) for key in GRAIN), raw, hashlib.sha256(raw).hexdigest()),
                )
                self.stats["projected_days"] += 1
                if self.stats["projected_days"] > self._day_limit:
                    raise SnapshotError("campaign_anomaly_day_full_population_binding")
            self._budget()
            database.commit()
            self._budget()
        self._resolved_day_count = self.stats["projected_days"]
        if self._resolved_day_count == 0 or (
            not isinstance(self.plan, CampaignAnomalyDayDiscoveryPlan)
            and self._resolved_day_count != self.plan.expected_days
        ):
            raise SnapshotError("campaign_anomaly_day_full_population_binding")
        self.native_days_sha256 = self._hash_days()
        self.parent._replay.check_parents()
        # These full source indexes only serve construction. Keep every day
        # while releasing their duplicate facts before downstream raw-DQ and
        # feature stages. FULL auto-vacuum avoids a second whole database copy.
        database.execute("BEGIN")
        database.execute("DROP TABLE rows")
        database.execute("DROP TABLE series")
        self._budget()
        database.commit()
        self._budget()
        self.stats["retained_index_bytes"] = max(
            self.path.stat().st_size, self.path.stat().st_blocks * 512
        )
        if self._hash_days() != self.native_days_sha256:
            raise CampaignAnomalyParentStateError("campaign_anomaly_day_retention_changed_days")
        self.days = _Days(self)

    def _series_tables(self, series: Grain, series_raw: bytes) -> dict[str, list[Row]]:
        tables: dict[str, list[Row]] = {name: [] for name in TABLES}
        size = count = 0

        def append(name: str, raw: bytes) -> None:
            nonlocal size, count
            size += len(raw)
            count += 1
            if count > self.plan.max_series_rows or size > self.plan.max_series_bytes:
                raise SnapshotError("campaign_anomaly_day_complete_series_budget")
            tables[name].append(decoded(raw, self._columns[name]))

        for name in ("inventory_sales", "return_events", "daily_demand_observations"):
            for (raw,) in self._db().execute(
                "SELECT payload FROM rows WHERE name=? AND series=? ORDER BY position",
                (name, series_raw),
            ):
                append(name, raw)
        # These are the same last-wins dictionaries used by native declarations.
        product = (
            self._db()
            .execute(
                "SELECT payload FROM rows WHERE name='product_catalog' AND natural=? ORDER BY position DESC LIMIT 1",
                (series[0],),
            )
            .fetchone()
        )
        if product is None:
            raise SnapshotError("campaign_anomaly_day_missing_native_product")
        append("product_catalog", product[0])
        rule = (
            self._db()
            .execute(
                "SELECT payload FROM rows WHERE name='return_policies' AND category=? AND channel=? ORDER BY position DESC LIMIT 1",
                (tables["product_catalog"][0]["category_id"], series[2]),
            )
            .fetchone()
        )
        if rule is not None:
            append("return_policies", rule[0])
        if tables["inventory_sales"] and rule is None:
            raise SnapshotError("campaign_anomaly_day_missing_native_return_rule")
        self.stats["maximum_series_rows"] = max(self.stats["maximum_series_rows"], count)
        self.stats["maximum_series_bytes"] = max(self.stats["maximum_series_bytes"], size)
        return tables

    def _hash_days(self) -> str:
        trace = hashlib.sha256()
        count = 0
        for *grain, raw, digest in self._db().execute(
            "SELECT event_type,business_date,product_id,location_id,channel,currency,payload,digest FROM days "
            "ORDER BY event_type,business_date,product_id,location_id,channel,currency"
        ):
            day = self._checked(raw, digest)
            if tuple(getattr(day, key) for key in GRAIN) != tuple(grain):
                raise CampaignAnomalyParentStateError("campaign_anomaly_day_private_grain_changed")
            trace.update(raw + b"\n")
            count += 1
        if count != self._resolved_day_count:
            raise CampaignAnomalyParentStateError("campaign_anomaly_day_private_extent_changed")
        return trace.hexdigest()
