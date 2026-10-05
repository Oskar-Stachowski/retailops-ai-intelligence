"""Conservative physical-series and knowledge-time index, without latest caching."""

from bisect import bisect_right
from collections import defaultdict
from datetime import UTC, datetime, time
from typing import Any

from retailops_ai.data_contracts.common import utc_time
from retailops_ai.stockout.features import FEATURE_TABLES, Records

Key = tuple[str, str | None, str | None]
Entry = tuple[datetime, int, dict[str, Any]]


class FactIndex:
    """Own a scalar-row snapshot; return the same eligible rows in original order.

    Both all known versions and all historical rows are retained for v1 lineage.
    Effective dates and latest-version selection remain in the causal projection.
    Routing is deliberately global and demand spans all selling channels/stocks.
    """

    def __init__(self, records: Records) -> None:
        if set(records) != set(FEATURE_TABLES):
            raise ValueError("stockout_features_require_only_allowlisted_fact_tables")
        buckets: dict[Key, list[Entry]] = defaultdict(list)
        for table in FEATURE_TABLES:
            for position, original in enumerate(records[table]):
                row = dict(original)
                available = row["curated_available_at"]
                if available is None:
                    continue
                times = [available]
                times.extend(
                    row[k]
                    for k in ("occurred_at", "snapshot_at", "ordered_at", "received_at", "known_at")
                    if row.get(k) is not None
                )
                if table == "daily_demand_versions":
                    times.append(datetime.combine(row["business_date"], time(), tzinfo=UTC))
                product = row["id"] if table == "product_catalog" else row.get("product_id")
                stock = (
                    None
                    if table in {"fulfillment_routes", "daily_demand_versions"}
                    else row.get("stock_location_id")
                )
                buckets[(table, product, stock)].append((max(times), position, row))
        self._buckets = {
            key: sorted(rows, key=lambda r: (r[0], r[1])) for key, rows in buckets.items()
        }
        self._times = {key: [r[0] for r in rows] for key, rows in self._buckets.items()}

    def known(self, product: str, stock: str, as_of: datetime) -> dict[str, list[dict[str, Any]]]:
        origin = utc_time(as_of)
        result = {}
        for table in FEATURE_TABLES:
            selected = []
            keys = {(table, p, s) for p in (None, product) for s in (None, stock)}
            for key in keys:
                rows = self._buckets.get(key, [])
                count = bisect_right(self._times.get(key, []), origin)
                selected.extend(rows[:count])
            # Catalog and snapshot ties must preserve v1 row order, not index order.
            result[table] = [dict(r[2]) for r in sorted(selected, key=lambda r: r[1])]
        return result
