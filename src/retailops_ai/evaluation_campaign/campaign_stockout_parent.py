"""Replay every public Source fact and stream all native stockout feature origins.

Internal adapter: the campaign must reserve the complete read before entry and
withhold its own output until this context exits successfully. This neither
reads private labels nor authorizes fitting or final access.
"""

import hashlib
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Self

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_context_facts import CampaignContextFacts
from retailops_ai.evaluation_campaign.campaign_stockout_parent_contract import (
    CampaignStockoutContextScope,
    CampaignStockoutParentPlan,
)
from retailops_ai.evaluation_campaign.source_replay import (
    _open_verified_source_parent,
    physical_limits,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.protocol import inspect_snapshot, verify_metadata
from retailops_ai.stockout_training.inputs import category_at


class CampaignStockoutPublicParent:
    def __init__(
        self, snapshot: Path, curated: Path, plan: CampaignStockoutParentPlan, scratch: Path
    ) -> None:
        self.snapshot, self.curated, self.scratch = snapshot, curated, scratch
        self.plan = CampaignStockoutParentPlan.model_validate_json(plan.model_dump_json())
        self._stack = ExitStack()
        self._used = self._active = self._started = self._exhausted = self._complete = False
        self._failed = False
        self._receipt: dict[str, Any] = {}

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("campaign_stockout_parent_single_use")
        self._used = True
        try:
            self._replay = self._stack.enter_context(
                _open_verified_source_parent(
                    self.snapshot,
                    self.curated,
                    self.plan.source,
                    limits=physical_limits(self.plan.source),
                    runtime=self.plan.runtime,
                )
            )
            snapshot = self._replay.curated.parent / "snapshot"
            inspected = inspect_snapshot(snapshot, False, physical_limits(self.plan.source))
            verify_metadata(snapshot, inspected, ("inventory_source",))
            parent = self.plan.source.parent
            scope = CampaignStockoutContextScope.model_validate_json(
                canonical_bytes(
                    dict(
                        data_seed=self.plan.source.source_parameters["seed"],
                        source_dataset_id=parent.source_dataset_id,
                        curated_dataset_id=parent.curated_dataset_id,
                        snapshot_id=parent.snapshot_id,
                    )
                )
            )
            self._facts = self._stack.enter_context(
                CampaignContextFacts(
                    self._replay.curated,
                    scope=scope,
                    policy=self.plan.storage,
                    scratch=self.scratch,
                )
            )
            self._active = True
        except BaseException:
            self._failed = True
            self._stack.close()
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        try:
            if args[0] is None:
                if self._failed or not self._exhausted:
                    raise SnapshotError("campaign_stockout_complete_origin_stream_required")
                self._replay.check_parents()
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
                self._active = False
        self._complete = args[0] is None and not self._failed

    def _check(self) -> None:
        if not self._active or self._failed:
            raise SnapshotError("campaign_stockout_parent_closed_or_failed")

    def rows(self) -> Iterator[dict[str, Any]]:
        """All physical keys, including ineligible/missing points, in native order."""
        self._check()
        if self._started:
            self._failed = True
            raise SnapshotError("campaign_stockout_origin_stream_single_use")
        self._started = True
        count = 0
        statuses: Counter[str] = Counter()
        trace, keys = hashlib.sha256(), hashlib.sha256()
        previous = None
        try:
            query = (
                "SELECT DISTINCT product,stock,json_extract(CAST(payload AS TEXT),'$.snapshot_at') "
                "FROM facts WHERE table_name='inventory_daily_snapshots' ORDER BY 1,2,3"
            )
            for product, stock, raw_origin in self._facts._connection().execute(query):
                self._check()
                origin = datetime.fromisoformat(raw_origin)
                key = product, stock, origin
                if previous is not None and key <= previous:
                    raise SnapshotError("campaign_stockout_duplicate_or_unsorted_physical_origin")
                previous = key
                count += 1
                if count > self.plan.max_origins:
                    raise SnapshotError("campaign_stockout_complete_origin_budget")
                point = self._facts.physical_point(product, stock, origin)
                catalog = [
                    row
                    for _, row, _ in self._facts._selected(
                        "product_catalog", product, None, origin.isoformat(timespec="microseconds")
                    )
                ]
                category, category_hash = category_at(catalog, product, origin)
                row = dict(
                    feature=point.model_dump(mode="json"),
                    category_id=category,
                    category_source_record_sha256=category_hash,
                )
                trace.update(canonical_bytes(row) + b"\n")
                keys.update(
                    canonical_bytes([product, stock, point.model_dump(mode="json")["as_of"]])
                    + b"\n"
                )
                statuses[point.status] += 1
                yield row
            if not count:
                raise SnapshotError("campaign_stockout_empty_physical_origin_population")
            self._check()
            self._exhausted = True
            self._receipt = dict(
                version="ai09-stockout-public-parent-receipt-1.0.0",
                plan_sha256=canonical_sha256(self.plan.model_dump(mode="json")),
                source=self.plan.source.model_dump(mode="json"),
                runtime=self.plan.runtime.model_dump(mode="json"),
                origins=count,
                feature_statuses=dict(sorted(statuses.items())),
                keys_sha256=keys.hexdigest(),
                feature_trace_sha256=trace.hexdigest(),
                snapshot_inventory_sha256=self._replay.snapshot_inventory_sha256,
                curated_inventory_sha256=self._replay.curated_inventory_sha256,
                logical_curated_sha256=self._replay.logical_curated_sha256,
                source_tables=self._replay.source_tables,
                source_rows=self._replay.source_rows,
                feature_facts_seal=deepcopy(self._facts.seal),
                stats=deepcopy(self._facts.stats),
                public_source_and_curated_replayed=True,
                native_inventory_metadata_verified=True,
                private_labels_read=False,
                upstream_forecast_prepared=False,
                mature_role_membership_verified=False,
                source_generation_ancestry_verified=False,
                journal_authorization_proven=False,
                quality_qualified=False,
                stage_ready=False,
            )
        except BaseException:
            self._failed = True
            raise

    def receipt(self) -> dict[str, Any]:
        if not self._complete or self._failed:
            raise SnapshotError("campaign_stockout_parent_not_completed")
        return deepcopy(self._receipt)
