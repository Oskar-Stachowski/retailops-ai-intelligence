"""Replay all original qualified labels against full public physical facts.

Internal operation only: reserve the whole public and private read in the
frozen campaign before entry. No receipt is valid before successful exit.
"""

import hashlib
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
from itertools import groupby
from pathlib import Path
from typing import Any, Literal, Self

from retailops_ai.data_contracts.common import Contract, utc_time
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_stockout_label_facts import (
    CampaignStockoutLabelFacts,
    CampaignStockoutLabelStoragePolicy,
    CampaignStockoutLabelStore,
)
from retailops_ai.evaluation_campaign.campaign_stockout_parent_contract import (
    CampaignStockoutContextScope,
)
from retailops_ai.evaluation_campaign.campaign_stockout_qualification import (
    CampaignStockoutQualification,
    CampaignStockoutQualificationPolicy,
)
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.evaluation_campaign.source_replay import (
    _open_verified_source_parent,
    physical_limits,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.protocol import inspect_snapshot, verify_metadata
from retailops_ai.stockout_label_partitions.bundle import DEFAULT_PARTITION_POLICY, LabelPreparation


class CampaignStockoutLabelPlan(Contract):
    version: Literal["ai09-full-stockout-label-replay-1.0.0"] = (
        "ai09-full-stockout-label-replay-1.0.0"
    )
    source: PhysicalSourceSpec
    runtime: PreparationRuntime
    storage: CampaignStockoutLabelStoragePolicy = CampaignStockoutLabelStoragePolicy()
    qualification: CampaignStockoutQualificationPolicy = CampaignStockoutQualificationPolicy()
    population: Literal["all_original_qualified_and_public_physical_origins_exact_join"] = (
        "all_original_qualified_and_public_physical_origins_exact_join"
    )
    projection: Literal["unchanged_ai08_label_window_and_label_preparation_point"] = (
        "unchanged_ai08_label_window_and_label_preparation_point"
    )


class CampaignStockoutLabels:
    def __init__(
        self,
        snapshot: Path,
        curated: Path,
        qualification: Path,
        plan: CampaignStockoutLabelPlan,
        scratch: Path,
        *,
        allow_evaluation_truth: bool = False,
    ) -> None:
        self.snapshot, self.curated, self.qualification, self.scratch = (
            snapshot,
            curated,
            qualification,
            scratch,
        )
        self.plan = CampaignStockoutLabelPlan.model_validate_json(plan.model_dump_json())
        self.allow_truth = allow_evaluation_truth
        self._plan_hash = canonical_sha256(self.plan.model_dump(mode="json"))
        self._stack = ExitStack()
        self._used = self._active = self._started = self._exhausted = self._complete = False
        self._failed = False
        self._receipt: dict[str, Any] = {}

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("campaign_stockout_labels_single_use")
        self._used = True
        if self.allow_truth is not True:
            raise SnapshotError("campaign_stockout_labels_private_opt_in_required")
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
            private = self._replay.curated.parent / "snapshot"
            snapshot = inspect_snapshot(private, False, physical_limits(self.plan.source))
            verify_metadata(private, snapshot, ("inventory_source",))
            self._snapshot_hash = canonical_sha256(snapshot.manifest)
            self._qualification = self._stack.enter_context(
                CampaignStockoutQualification(
                    self.qualification,
                    snapshot,
                    allow_evaluation_truth=True,
                    policy=self.plan.qualification,
                )
            )
            parent = self.plan.source.parent
            scope = CampaignStockoutContextScope.model_validate_json(
                canonical_bytes(
                    dict(
                        data_seed=self.plan.source.source_parameters["seed"],
                        source_dataset_id=parent.source_dataset_id,
                        snapshot_id=parent.snapshot_id,
                        curated_dataset_id=parent.curated_dataset_id,
                    )
                )
            )
            self._facts = self._stack.enter_context(
                CampaignStockoutLabelStore(
                    self._replay.curated,
                    scope=scope,
                    policy=self.plan.storage,
                    scratch=self.scratch,
                )
            )
            self._native = CampaignStockoutLabelFacts(self._facts, snapshot)
            self._active = True
        except BaseException:
            self._failed = True
            self._stack.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        try:
            if args[0] is None:
                if self._failed or not self._exhausted:
                    raise SnapshotError("campaign_stockout_labels_complete_stream_required")
                if self._plan_hash != canonical_sha256(self.plan.model_dump(mode="json")):
                    raise SnapshotError("campaign_stockout_labels_plan_changed")
                if self._snapshot_hash != canonical_sha256(self._native.snapshot.manifest):
                    raise SnapshotError("campaign_stockout_labels_snapshot_changed")
                self._replay.check_parents()
        except BaseException:
            self._failed = True
            self._stack.__exit__(*sys.exc_info())
            raise
        else:
            try:
                self._stack.__exit__(*args)
            except BaseException:
                self._failed = True
                raise
        finally:
            self._active = False
        self._complete = args[0] is None and not self._failed
        if self._complete:
            self._receipt["original_qualification"] = self._qualification.receipt()

    def rows(self) -> Iterator[dict[str, Any]]:
        if not self._active or self._started or self._failed:
            self._failed = True
            raise SnapshotError("campaign_stockout_labels_closed_or_repeated_stream")
        self._started = True
        count = 0
        statuses: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        trace, keys = hashlib.sha256(b"["), hashlib.sha256()
        preparation = LabelPreparation(self._native, DEFAULT_PARTITION_POLICY)
        try:
            origins = iter(
                self._facts._connection().execute(
                    "SELECT product,stock,ready FROM facts WHERE table_name='inventory_daily_snapshots' "
                    "ORDER BY product,stock,ready,position"
                )
            )
            for (product, stock), windows in groupby(
                self._qualification.rows(), key=lambda w: (w["product_id"], w["stock_location_id"])
            ):
                ledger = self._native.ledger(product, stock)
                for window in windows:
                    if not self._active:
                        raise SnapshotError("campaign_stockout_labels_closed_stream")
                    stamp = utc_time(datetime.fromisoformat(window["origin"])).isoformat(
                        timespec="microseconds"
                    )
                    if next(origins, None) != (product, stock, stamp):
                        raise SnapshotError(
                            "campaign_stockout_labels_complete_physical_origin_mismatch"
                        )
                    point = preparation.point(window, ledger)
                    if point.reason != window["reason"]:
                        raise SnapshotError("campaign_stockout_labels_qualified_reason_mismatch")
                    row = point.model_dump(mode="json")
                    if count:
                        trace.update(b",")
                    trace.update(canonical_bytes(row))
                    keys.update(canonical_bytes([product, stock, row["as_of"]]) + b"\n")
                    count += 1
                    statuses[point.status] += 1
                    if point.reason is not None:
                        reasons[point.reason] += 1
                    yield row
                del ledger
            if not count or next(origins, None) is not None:
                raise SnapshotError("campaign_stockout_labels_complete_physical_origin_mismatch")
            trace.update(b"]")
            self._exhausted = True
            self._receipt = dict(
                version="ai09-full-stockout-label-replay-receipt-1.0.0",
                plan_sha256=self._plan_hash,
                source=self.plan.source.model_dump(mode="json"),
                runtime=self.plan.runtime.model_dump(mode="json"),
                rows=count,
                statuses=dict(sorted(statuses.items())),
                reasons=dict(sorted(reasons.items())),
                points_sha256=trace.hexdigest(),
                keys_sha256=keys.hexdigest(),
                snapshot_inventory_sha256=self._replay.snapshot_inventory_sha256,
                curated_inventory_sha256=self._replay.curated_inventory_sha256,
                logical_curated_sha256=self._replay.logical_curated_sha256,
                label_facts_seal=deepcopy(self._facts.seal),
                stats=deepcopy(self._facts.stats),
                public_source_and_curated_replayed=True,
                ledger_replay_verified=True,
                complete_physical_origin_join_verified=True,
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
            raise SnapshotError("campaign_stockout_labels_not_completed")
        return deepcopy(self._receipt)
