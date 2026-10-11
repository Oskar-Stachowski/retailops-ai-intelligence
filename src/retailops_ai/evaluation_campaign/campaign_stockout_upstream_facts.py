"""Full AI09 sealed upstream facts with unchanged AI08 physical-series queries."""

import hashlib
import sqlite3
from collections import OrderedDict
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from retailops_ai.curated.contract import columns_for, decoded
from retailops_ai.evaluation_campaign.campaign_context_facts import CampaignContextFacts
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.stockout.upstream import UPSTREAM_TABLES
from retailops_ai.stockout_upstream_series.store import SeriesFacts, indexed
from retailops_ai.stockout_upstream_storage.store import UpstreamStoragePolicy, keys


class CampaignStockoutUpstreamFacts(CampaignContextFacts):
    fact_tables = UPSTREAM_TABLES
    fact_keys = staticmethod(keys)


class _NativeIndexPolicy(UpstreamStoragePolicy):
    version: Literal["ai09-full-stockout-upstream-index-1.0.0"] = (
        "ai09-full-stockout-upstream-index-1.0.0"  # type: ignore[assignment]
    )
    max_db_bytes: Annotated[int, Field(ge=4096, le=16 * 1024**3)]
    max_selected_rows: Annotated[int, Field(ge=1, le=1_000_000)]
    max_selected_bytes: Annotated[int, Field(ge=1, le=512 * 1024**2)]


class CampaignStockoutUpstreamIndex(SeriesFacts):
    """Only the active verified facts owner opens storage. No legacy parent caps.

    Native global-version validation, physical selection and bounded decoding
    are inherited unchanged. The index builder retains every known revision.
    """

    def __init__(self, facts: CampaignStockoutUpstreamFacts) -> None:
        super().__init__(
            facts.curated,
            policy=_NativeIndexPolicy(
                max_db_bytes=facts.policy.max_index_bytes,
                max_selected_rows=facts.policy.max_selected_rows,
                max_selected_bytes=facts.policy.max_selected_bytes,
                batch_rows=facts.policy.batch_rows,
            ),
        )
        self._facts = facts
        self._used = True
        self.path = facts.path
        self.document = facts.document
        self.stats = facts.stats
        self.seal = dict(facts.seal)
        self._build()

    def _connection(self) -> sqlite3.Connection:
        return self._facts._connection()

    def _open(self) -> None:
        raise SnapshotError("campaign_stockout_upstream_requires_active_verified_facts")

    def check_parent(self) -> None:
        self._sealed_connection()
        self._facts.check_parent()

    def _build(self) -> None:
        db = self._connection()
        db.execute("PRAGMA query_only=OFF")
        db.execute(
            "CREATE TABLE series_index(table_name TEXT, position INTEGER, ready TEXT, "
            "logical_key TEXT, version INTEGER, product TEXT, selling TEXT, channel TEXT, "
            "category TEXT, stock TEXT, business_date TEXT, effective_from TEXT, "
            "effective_to TEXT, PRIMARY KEY(table_name,position))"
        )
        digest = hashlib.sha256()
        self._columns = {
            table: columns_for(table, self.document["schema_version"]) for table in UPSTREAM_TABLES
        }
        for table in UPSTREAM_TABLES:
            columns = self._columns[table]
            for position, ready, raw, checksum in db.execute(
                "SELECT position,ready,payload,checksum FROM facts WHERE table_name=? ORDER BY position",
                (table,),
            ):
                if hashlib.sha256(raw).hexdigest() != checksum:
                    raise SnapshotError("stockout_upstream_series_row_checksum_mismatch")
                row = decoded(raw, columns)
                if keys(table, row) != (None, None, ready):
                    raise SnapshotError("stockout_upstream_series_row_index_mismatch")
                index = (table, position, ready, *indexed(table, row))
                db.execute("INSERT INTO series_index VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", index)
                digest.update(canonical_json([*index, checksum]) + b"\n")
                if position % 256 == 0:
                    self._facts._budget()
        db.execute(
            "CREATE INDEX series_versions ON series_index(table_name,logical_key,ready,version)"
        )
        db.execute("CREATE INDEX series_products ON series_index(table_name,product,ready)")
        db.execute(
            "CREATE INDEX series_selling ON series_index(table_name,selling,channel,ready,business_date)"
        )
        db.execute(
            "CREATE INDEX series_routes ON series_index(table_name,stock,ready,effective_from,effective_to)"
        )
        db.commit()
        db.execute("PRAGMA query_only=ON")
        self.seal = {**self.seal, "series_index_sha256": digest.hexdigest()}
        self.stats["database_bytes"] = self.path.stat().st_size
        self.stats["globally_validated_origins"] = 0
        self.stats["series_reads"] = 0
        self.stats["decoded_cache_hits"] = 0
        self.stats["maximum_decoded_cache_rows"] = 0
        self.stats["maximum_decoded_cache_bytes"] = 0
        self._decoded_cache: OrderedDict[tuple[str, int], tuple[dict[str, Any], int]] = (
            OrderedDict()
        )
        self._decoded_cache_bytes = 0
        self._facts._budget()
        self._database_identity = self._identity()
        self._validated_origin: datetime | None = None
