"""Bounded, complete version validation without a duplicate qualified-body table."""

import hashlib
import sqlite3
from collections import OrderedDict
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from retailops_ai.curated.builder import iter_rows
from retailops_ai.curated.contract import columns_for, decoded, encoded
from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.label_contract import DemandVersion
from retailops_ai.evaluation_campaign.source_version_contract import ForecastSourceVersion
from retailops_ai.evaluation_campaign.source_versions import GRAIN, _records
from retailops_ai.source_snapshot.files import SnapshotError


def check_index(db: sqlite3.Connection, maximum: int) -> None:
    db.commit()
    used = db.execute(
        "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"
    ).fetchone()[0]
    if used > maximum:
        raise SnapshotError("physical_forecast_index_budget")


class PhysicalVersionIndex:
    """Internal index, owned by one active private source replay and connection.

    Validate every observation and history, including rows outside all roles,
    before querying any target. Reconstruct the same temporal quality records
    as the legacy reader; never promote an older incomplete version to complete.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        curated: Path,
        manifest: dict[str, Any],
        *,
        maximum_rows: int,
        maximum_bytes: int,
    ) -> None:
        self.db = db
        self.version = str(manifest["schema_version"])
        self.maximum_bytes = maximum_bytes
        self._cache: OrderedDict[bytes, tuple[ForecastSourceVersion, ...]] = OrderedDict()
        db.execute("CREATE TABLE observations(id TEXT PRIMARY KEY, grain BLOB UNIQUE, body BLOB)")
        db.execute(
            "CREATE TABLE versions(observation_id TEXT, version INTEGER, body BLOB, "
            "PRIMARY KEY(observation_id,version))"
        )
        specs = {t["table"]: t for t in manifest["tables"]}
        counts: dict[str, int] = {}
        for table in ("daily_demand_observations", "daily_demand_versions"):
            count = 0
            for row in iter_rows(curated, specs[table]["files"], 256):
                count += 1
                raw = encoded(row)
                if count > maximum_rows or len(raw) > 65536:
                    raise SnapshotError("physical_forecast_version_input_budget")
                if table == "daily_demand_observations":
                    grain = canonical_bytes(
                        [row[k].isoformat() if k == "business_date" else row[k] for k in GRAIN]
                    )
                    db.execute("INSERT INTO observations VALUES (?,?,?)", (row["id"], grain, raw))
                else:
                    db.execute(
                        "INSERT INTO versions VALUES (?,?,?)",
                        (row["observation_id"], row["version"], raw),
                    )
                if count % 256 == 0:
                    check_index(db, maximum_bytes)
            if count != specs[table]["row_count"] or not count:
                raise SnapshotError("physical_forecast_version_input_count")
            counts[table] = count
        if db.execute(
            "SELECT 1 FROM versions v LEFT JOIN observations o ON v.observation_id=o.id "
            "WHERE o.id IS NULL LIMIT 1"
        ).fetchone():
            raise SnapshotError("physical_forecast_orphan_history")
        digest = hashlib.sha256()
        verified = 0
        for identity, raw in db.execute("SELECT id,body FROM observations ORDER BY grain"):
            for record in self._joined(identity, raw):
                digest.update(canonical_bytes(record.model_dump(mode="json")) + b"\n")
                verified += 1
        if verified != counts["daily_demand_versions"]:
            raise SnapshotError("physical_forecast_version_output_count")
        check_index(db, maximum_bytes)
        self.observation_rows = counts["daily_demand_observations"]
        self.version_rows = verified
        self.version_inventory_sha256 = digest.hexdigest()

    def _joined(self, identity: str, raw: bytes) -> Iterator[ForecastSourceVersion]:
        versions = [
            decoded(row[0], columns_for("daily_demand_versions", self.version))
            for row in self.db.execute(
                "SELECT body FROM versions WHERE observation_id=? ORDER BY version LIMIT 9",
                (identity,),
            )
        ]
        yield from _records(
            decoded(raw, columns_for("daily_demand_observations", self.version)), versions
        )

    def _for_grain(self, grain: bytes) -> tuple[ForecastSourceVersion, ...]:
        if grain in self._cache:
            self._cache.move_to_end(grain)
            return self._cache[grain]
        found = self.db.execute(
            "SELECT id,body FROM observations WHERE grain=?", (grain,)
        ).fetchone()
        result = tuple(self._joined(*found)) if found is not None else ()
        self._cache[grain] = result
        if len(self._cache) > 128:
            self._cache.popitem(last=False)
        return result

    def candidates(self, key: ForecastKey, cutoff: datetime) -> tuple[DemandVersion, ...]:
        grain = canonical_bytes(
            [key.target_date.isoformat(), key.product_id, key.selling_location_id, key.channel]
        )
        return tuple(r.at_cutoff(cutoff) for r in self._for_grain(grain))

    def clear_cache(self) -> None:
        self._cache.clear()
