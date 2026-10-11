"""Bounded physical label queries over a complete, sealed public Source replay.

These lookups intentionally include future outcomes and belong only to the
private label lane. They must never be used to construct model features.
"""

import hashlib
from datetime import datetime
from typing import Any, Literal

from retailops_ai.curated.contract import decoded
from retailops_ai.data_contracts.common import utc_time
from retailops_ai.evaluation_campaign.campaign_context_facts import CampaignContextFacts
from retailops_ai.evaluation_campaign.campaign_segment_contract import CampaignContextStoragePolicy
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.protocol import Snapshot
from retailops_ai.stockout.contract import DEFAULT_POLICY, LedgerMovement
from retailops_ai.stockout_label_partitions.store import TABLES, LabelFacts


def label_keys(table: str, row: dict[str, Any]) -> tuple[str, str | None, str]:
    if table == "inventory_history_coverage":
        return row["coverage_id"], None, ""
    stamp = (
        utc_time(row["snapshot_at"]).isoformat(timespec="microseconds")
        if table == "inventory_daily_snapshots"
        else ""
    )
    return row["product_id"], row["stock_location_id"], stamp


class CampaignStockoutLabelStoragePolicy(CampaignContextStoragePolicy):
    """Same bounded storage resources, explicitly for outcomes rather than PIT features."""

    version: Literal["ai09-full-stockout-label-facts-1.0.0"] = (
        "ai09-full-stockout-label-facts-1.0.0"  # type: ignore[assignment]
    )
    projection: Literal["closed_ai08_label_preparation_point_unchanged"] = (
        "closed_ai08_label_preparation_point_unchanged"  # type: ignore[assignment]
    )
    series_index: Literal["one_complete_physical_ledger_no_origin_knowledge_clipping"] = (
        "one_complete_physical_ledger_no_origin_knowledge_clipping"  # type: ignore[assignment]
    )


class CampaignStockoutLabelStore(CampaignContextFacts):
    fact_tables = TABLES
    fact_keys = staticmethod(label_keys)
    policy_model = CampaignStockoutLabelStoragePolicy


class CampaignStockoutLabelFacts(LabelFacts):
    """Reuse the closed native point projection without its old whole-input caps.

    The unchanged 100000-movement limit applies to one physical series. The
    complete population and index have separate explicit AI09 budgets.
    """

    def __init__(self, owner: CampaignStockoutLabelStore, snapshot: Snapshot) -> None:
        super().__init__(owner.curated, DEFAULT_POLICY)
        self._used = True
        self._owner = owner
        self.snapshot = snapshot
        projection = snapshot.manifest["source"]["descriptor"]["context"]["projection"]
        if (
            any(
                projection[k] != getattr(self.label_policy, k)
                for k in ("stock_measure", "reservation_policy", "episode_policy")
            )
            or projection["diagnostic_horizon_days"] != self.label_policy.horizon_days
        ):
            raise SnapshotError("campaign_stockout_label_policy_source_mismatch")

    def _open(self) -> None:
        raise SnapshotError("campaign_stockout_labels_require_active_verified_store")

    def _record_selection(self, rows: int, size: int) -> None:
        self._owner.stats["maximum_selected_rows"] = max(
            self._owner.stats["maximum_selected_rows"], rows
        )
        self._owner.stats["maximum_selected_bytes"] = max(
            self._owner.stats["maximum_selected_bytes"], size
        )

    def get(self, name: str, *key: Any) -> dict[str, Any] | None:
        if name != "inventory_history_coverage" or len(key) != 1:
            raise SnapshotError("campaign_stockout_label_coverage_lookup_required")
        selected = self._owner._selected(
            name, key[0], None, "", remaining_rows=min(2, self._owner.policy.max_selected_rows)
        )
        if len(selected) > 1:
            raise SnapshotError("campaign_stockout_label_ambiguous_coverage")
        self._record_selection(len(selected), sum(r[2] for r in selected))
        return selected[0][1] if selected else None

    def snapshot_at(self, product: str, stock: str, origin: datetime) -> dict[str, Any] | None:
        table = "inventory_daily_snapshots"
        stamp = utc_time(origin).isoformat(timespec="microseconds")
        entries = (
            self._owner._connection()
            .execute(
                "SELECT ready,payload,checksum FROM facts WHERE table_name=? AND product=? "
                "AND stock=? AND ready=? ORDER BY position LIMIT 2",
                (table, product, stock, stamp),
            )
            .fetchall()
        )
        if len(entries) > 1:
            raise SnapshotError("stockout_label_ambiguous_origin_snapshot")
        if not entries:
            return None
        ready, raw, checksum = entries[0]
        if (
            len(raw) > self._owner.policy.max_selected_bytes
            or hashlib.sha256(raw).hexdigest() != checksum
        ):
            raise SnapshotError("campaign_stockout_label_snapshot_resource_or_checksum")
        row = decoded(raw, self._owner._columns[table])
        if label_keys(table, row) != (product, stock, ready):
            raise SnapshotError("campaign_stockout_label_snapshot_index_mismatch")
        self._record_selection(1, len(raw))
        return row

    def ledger(self, product: str, stock: str) -> list[LedgerMovement]:
        # Only one bounded physical series is decoded, in original Source order.
        maximum = min(self._owner.policy.max_selected_rows, self.label_policy.max_ledger_rows)
        count = (
            self._owner._connection()
            .execute(
                "SELECT count(*) FROM (SELECT 1 FROM facts WHERE table_name='inventory_ledger' "
                "AND product=? AND stock=? AND ready='' LIMIT ?)",
                (product, stock, maximum + 1),
            )
            .fetchone()[0]
        )
        if count > maximum:
            raise SnapshotError("campaign_context_facts_selected_resource_limit")
        selected = self._owner._selected(
            "inventory_ledger",
            product,
            stock,
            "",
            remaining_rows=maximum,
        )
        result = []
        size = 0
        for _, row, length in selected:
            size += length
            result.append(
                LedgerMovement.model_validate(
                    {
                        **{
                            k: row[k]
                            for k in (
                                "product_id",
                                "stock_location_id",
                                "occurred_at",
                                "available_at",
                                "sequence",
                                "quantity_delta",
                                "movement_type",
                            )
                        },
                        "event_id": row["inventory_event_id"],
                    }
                )
            )
        self._record_selection(len(result), size)
        return result
