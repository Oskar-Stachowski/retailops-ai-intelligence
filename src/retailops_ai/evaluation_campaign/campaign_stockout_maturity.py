"""Join complete public features and original labels with explicit mature roles.

This internal reader requires an enclosing reservation for the full public and
private populations. Consumers must spool rows and accept the completion receipt
before fitting; yielding a row is not permission to fit or publish it.
"""

import hashlib
import os
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from itertools import zip_longest
from pathlib import Path
from typing import Any, BinaryIO, Self

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_stockout_labels import CampaignStockoutLabels
from retailops_ai.evaluation_campaign.campaign_stockout_maturity_contract import (
    STOCKOUT_ROLES,
    CampaignStockoutMaturityPlan,
    CampaignStockoutMaturityPolicy,
    start_of_day,
)
from retailops_ai.evaluation_campaign.campaign_stockout_public import verify_public_stockout
from retailops_ai.evaluation_campaign.campaign_stockout_public_contract import (
    CampaignStockoutPublicRow,
)
from retailops_ai.source_snapshot.files import SnapshotError, inventory, read_bytes, regular_file
from retailops_ai.stockout.contract import LabelPoint


def _membership(
    feature: CampaignStockoutPublicRow, label: LabelPoint, policy: CampaignStockoutMaturityPolicy
) -> dict[str, Any]:
    position = next(
        (
            i
            for i, window in enumerate(policy.windows)
            if window.origins.start <= label.as_of.date() <= window.origins.end
        ),
        None,
    )
    window = policy.windows[position] if position is not None else None
    reason = None
    if window is None:
        reason = "outside_development_roles"
    elif feature.status != "eligible":
        reason = "feature_" + (feature.reason or feature.status)
    elif label.status != "evaluable":
        reason = "label_" + (label.reason or label.status)
    elif (
        label.label_available_at is None or label.label_available_at > window.label_knowledge_cutoff
    ):
        reason = "label_not_available_at_role_cutoff"
    elif position is not None and position + 1 < len(policy.windows):
        boundary = start_of_day(policy.windows[position + 1])
        if label.window_end_at >= boundary or label.label_available_at >= boundary:
            reason = "purged_boundary_or_delayed_label"
    return dict(
        role=STOCKOUT_ROLES[position] if position is not None else "purged",
        development_window=window.role if window is not None else None,
        eligible=reason is None,
        reason=reason,
        label_knowledge_cutoff=window.model_dump(mode="json")["label_knowledge_cutoff"]
        if window is not None
        else None,
    )


def _identity(stat: os.stat_result) -> tuple[int, ...]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class CampaignStockoutMatureRows:
    def __init__(
        self,
        public: Path,
        snapshot: Path,
        curated: Path,
        qualification: Path,
        plan: CampaignStockoutMaturityPlan,
        scratch: Path,
        *,
        allow_evaluation_truth: bool = False,
    ) -> None:
        self.public, self.snapshot, self.curated, self.qualification, self.scratch = (
            public,
            snapshot,
            curated,
            qualification,
            scratch,
        )
        self.plan = CampaignStockoutMaturityPlan.model_validate(plan.model_dump(mode="python"))
        self._plan_hash = canonical_sha256(self.plan.model_dump(mode="json"))
        self.allow_truth = allow_evaluation_truth
        self._stack = ExitStack()
        self._used = self._active = self._started = self._exhausted = self._complete = False
        self._failed = False
        self._receipt: dict[str, Any] = {}

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("campaign_stockout_maturity_single_use")
        self._used = True
        if self.allow_truth is not True:
            raise SnapshotError("campaign_stockout_maturity_private_opt_in_required")
        try:
            self._manifest = verify_public_stockout(
                self.public,
                expected_dataset_id=self.plan.public_dataset_id,
                expected_plan=self.plan.public,
            )
            self._manifest_bytes = canonical_bytes(self._manifest) + b"\n"
            if self._manifest["descriptor"]["origins"] > self.plan.max_rows:
                raise SnapshotError("campaign_stockout_maturity_full_population_budget")
            # Close the label owner first, then check the still-open public
            # artifact. Changes during label-parent verification must fail too.
            self._stream = self._stack.enter_context(self._public_lifetime())
            self._labels = self._stack.enter_context(
                CampaignStockoutLabels(
                    self.snapshot,
                    self.curated,
                    self.qualification,
                    self.plan.labels,
                    self.scratch,
                    allow_evaluation_truth=True,
                )
            )
            self._active = True
        except BaseException:
            self._failed = True
            self._stack.__exit__(*sys.exc_info())
            raise
        return self

    @contextmanager
    def _public_lifetime(self) -> Iterator[BinaryIO]:
        with regular_file(self.public, "rows.jsonl") as stream:
            self._public_identity = _identity(os.fstat(stream.fileno()))
            yield stream
            self._check_public()

    def _check_public(self) -> None:
        if self._plan_hash != canonical_sha256(self.plan.model_dump(mode="json")):
            raise SnapshotError("campaign_stockout_maturity_plan_changed")
        inventory(self.public, {"manifest.json", "rows.jsonl"})
        if (
            read_bytes(self.public, "manifest.json") != self._manifest_bytes
            or canonical_bytes(self._manifest) + b"\n" != self._manifest_bytes
            or _identity(os.fstat(self._stream.fileno())) != self._public_identity
            or _identity((self.public / "rows.jsonl").stat()) != self._public_identity
        ):
            raise SnapshotError("campaign_stockout_maturity_public_changed")

    def __exit__(self, *args: Any) -> None:
        try:
            if args[0] is None:
                if self._failed or not self._exhausted:
                    raise SnapshotError("campaign_stockout_maturity_complete_stream_required")
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
        if args[0] is None and not self._failed:
            try:
                original = self._labels.receipt()
                if (
                    original["rows"] != self._receipt["rows"]
                    or original["points_sha256"] != self._consumed_labels_sha256
                    or original["keys_sha256"] != self._manifest["descriptor"]["keys_sha256"]
                ):
                    raise SnapshotError(
                        "campaign_stockout_maturity_original_label_receipt_mismatch"
                    )
                self._receipt["original_labels"] = original
            except BaseException:
                self._failed = True
                raise
            self._complete = True

    def _public_rows(self) -> Iterator[CampaignStockoutPublicRow]:
        count = size = 0
        digest, keys = hashlib.sha256(), hashlib.sha256()
        previous = None
        while raw := self._stream.readline(self.plan.public.max_record_bytes + 1):
            if len(raw) > self.plan.public.max_record_bytes:
                raise SnapshotError("campaign_stockout_maturity_public_record_budget")
            row = CampaignStockoutPublicRow.model_validate_json(raw)
            if canonical_bytes(row.model_dump(mode="json")) + b"\n" != raw:
                raise SnapshotError("campaign_stockout_maturity_public_noncanonical_row")
            key = row.product_id, row.stock_location_id, row.as_of
            if previous is not None and key <= previous:
                raise SnapshotError("campaign_stockout_maturity_public_key_order")
            previous = key
            digest.update(raw)
            keys.update(canonical_bytes([*key[:2], row.model_dump(mode="json")["as_of"]]) + b"\n")
            count += 1
            size += len(raw)
            if count > self.plan.max_rows or size > self.plan.public.max_artifact_bytes:
                raise SnapshotError("campaign_stockout_maturity_full_population_budget")
            yield row
        descriptor = self._manifest["descriptor"]
        if (count, size, digest.hexdigest(), keys.hexdigest()) != (
            descriptor["origins"],
            descriptor["rows_bytes"],
            descriptor["rows_sha256"],
            descriptor["keys_sha256"],
        ):
            raise SnapshotError("campaign_stockout_maturity_complete_public_stream_mismatch")

    def rows(self) -> Iterator[dict[str, Any]]:
        if not self._active or self._started or self._failed:
            self._failed = True
            raise SnapshotError("campaign_stockout_maturity_closed_or_repeated_stream")
        self._started = True
        count = size = 0
        trace = hashlib.sha256()
        labels_trace = hashlib.sha256(b"[")
        role_keys = {role: hashlib.sha256(b"[") for role in STOCKOUT_ROLES}
        role_labels = {role: hashlib.sha256(b"[") for role in STOCKOUT_ROLES}
        role_rows = {role: hashlib.sha256() for role in STOCKOUT_ROLES}
        eligible: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        classes: dict[str, Counter[int]] = {role: Counter() for role in STOCKOUT_ROLES}
        try:
            for feature, raw in zip_longest(self._public_rows(), self._labels.rows()):
                if not self._active:
                    raise SnapshotError("campaign_stockout_maturity_closed_stream")
                if feature is None or raw is None:
                    raise SnapshotError("campaign_stockout_maturity_complete_physical_origin_join")
                label = LabelPoint.model_validate_json(canonical_bytes(raw))
                if (feature.product_id, feature.stock_location_id, feature.as_of) != (
                    label.product_id,
                    label.stock_location_id,
                    label.as_of,
                ):
                    raise SnapshotError("campaign_stockout_maturity_complete_physical_origin_join")
                membership = _membership(feature, label, self.plan.policy)
                row = dict(
                    membership=membership,
                    feature=feature.model_dump(mode="json"),
                    label=raw,
                )
                body = canonical_bytes(row) + b"\n"
                count += 1
                size += len(body)
                if (
                    count > self.plan.max_rows
                    or size > self.plan.max_stream_bytes
                    or len(body) > self.plan.max_record_bytes
                ):
                    raise SnapshotError("campaign_stockout_maturity_full_population_budget")
                if count > 1:
                    labels_trace.update(b",")
                labels_trace.update(canonical_bytes(raw))
                if membership["eligible"]:
                    if label.incident_stockout is None:
                        raise SnapshotError("campaign_stockout_maturity_unknown_is_not_negative")
                    role = membership["role"]
                    key = [label.product_id, label.stock_location_id, raw["as_of"]]
                    if eligible[role]:
                        role_keys[role].update(b",")
                        role_labels[role].update(b",")
                    role_keys[role].update(canonical_bytes(key))
                    role_labels[role].update(canonical_bytes([*key, label.incident_stockout]))
                    role_rows[role].update(body)
                    eligible[role] += 1
                    classes[role][label.incident_stockout] += 1
                else:
                    reasons[membership["reason"]] += 1
                trace.update(body)
                yield row
            if not count:
                raise SnapshotError("campaign_stockout_maturity_empty_population")
            labels_trace.update(b"]")
            self._consumed_labels_sha256 = labels_trace.hexdigest()
            for role in STOCKOUT_ROLES:
                role_keys[role].update(b"]")
                role_labels[role].update(b"]")
            self._exhausted = True
            self._receipt = dict(
                version="ai09-stockout-full-mature-development-receipt-1.0.0",
                plan_sha256=self._plan_hash,
                public_dataset_id=self.plan.public_dataset_id,
                rows=count,
                stream_bytes=size,
                stream_sha256=trace.hexdigest(),
                eligible_by_role={role: eligible[role] for role in STOCKOUT_ROLES},
                eligible_role_traces={
                    role: dict(
                        keys_sha256=role_keys[role].hexdigest(),
                        labels_sha256=role_labels[role].hexdigest(),
                        joined_rows_sha256=role_rows[role].hexdigest(),
                    )
                    for role in STOCKOUT_ROLES
                },
                classes_by_role={
                    role: {str(value): classes[role][value] for value in (0, 1)}
                    for role in STOCKOUT_ROLES
                },
                ineligible_reasons=dict(sorted(reasons.items())),
                complete_physical_origin_join_verified=True,
                mature_role_membership_verified=True,
                source_generation_ancestry_verified=False,
                journal_authorization_proven=False,
                fitted_models=0,
                quality_qualified=False,
                stage_ready=False,
            )
        except BaseException:
            self._failed = True
            raise

    def receipt(self) -> dict[str, Any]:
        if not self._complete or self._failed:
            raise SnapshotError("campaign_stockout_maturity_not_completed")
        return deepcopy(self._receipt)
