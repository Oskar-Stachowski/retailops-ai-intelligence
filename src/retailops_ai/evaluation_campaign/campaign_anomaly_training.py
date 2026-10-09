"""Full native membership to numerical fit, with bounded private role files.

The caller owns Source authorization, the complete parent contexts and the
Project attempt journal. A returned numerical result is not their acceptance.
"""

import hashlib
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from retailops_ai.anomaly_detectors.census_contract import (
    CensusFitPolicy,
    CensusGroup,
    CensusPipeline,
)
from retailops_ai.anomaly_detectors.census_fit import (
    census_capacity_threshold,
    fit_census_pipeline,
)
from retailops_ai.anomaly_detectors.codec import baseline_score, forest_scores
from retailops_ai.anomaly_detectors.contract import Resources
from retailops_ai.anomaly_detectors.protocol import Membership, scoring_origin, series_key
from retailops_ai.anomaly_detectors.rows import CountRateRow, NumericalRow
from retailops_ai.anomaly_portfolio.model import EventCapacity
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_anomaly_membership import (
    AnomalyMembershipRow,
    CampaignAnomalyMembershipPlan,
)
from retailops_ai.qualified_anomalies.contract import Policy
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, regular_file

MAX_ROW_BYTES = 32768


@dataclass(frozen=True)
class AnomalyCensusTraining:
    membership_plan_sha256: str
    training_membership_sha256: str
    validation_membership_sha256: str
    requested_rows: int
    # Counts include unknown and excluded memberships in all four roles.
    membership_counts: tuple[tuple[str, bool, int], ...]
    groups: tuple[CensusGroup, ...]
    fit_resources: tuple[Resources, ...]
    role_file_bytes: int


@dataclass(frozen=True)
class _RoleFile:
    path: Path
    count: int
    bytes: int
    sha256: str

    def rows(self) -> Iterator[NumericalRow]:
        digest = hashlib.sha256()
        count = size = 0
        try:
            with regular_file(self.path.parent, self.path.name) as stream:
                while raw := stream.readline(MAX_ROW_BYTES + 1):
                    count += 1
                    size += len(raw)
                    if count > self.count or size > self.bytes or len(raw) > MAX_ROW_BYTES:
                        raise SnapshotError("campaign_anomaly_training_role_extent")
                    row = CountRateRow.model_validate_json(raw)
                    if raw != canonical_json(row.model_dump(mode="json")) + b"\n":
                        raise SnapshotError("campaign_anomaly_training_role_encoding")
                    digest.update(raw)
                    yield row
        except OSError as exc:
            raise SnapshotError("campaign_anomaly_training_role_io") from exc
        if (count, size, digest.hexdigest()) != (self.count, self.bytes, self.sha256):
            raise SnapshotError("campaign_anomaly_training_role_identity")


def _forest_scores(pipeline: CensusPipeline, validation: _RoleFile) -> Iterator[float]:
    batch: list[NumericalRow] = []
    for row in validation.rows():
        batch.append(row)
        if len(batch) == 8192:
            yield from forest_scores(pipeline, batch)
            batch.clear()
    if batch:
        yield from forest_scores(pipeline, batch)


def _expected(plan: CampaignAnomalyMembershipPlan) -> Iterator[tuple[tuple[str, ...], str]]:
    for scope in plan.scopes:
        native = plan.native_protocol(scope)
        for offset in range((plan.test.end - plan.train.start).days + 1):
            day = plan.train.start + timedelta(days=offset)
            yield (*series_key(scope), day.isoformat()), native.role(day)


def fit_anomaly_membership_census(
    rows: Iterable[AnomalyMembershipRow],
    plan: CampaignAnomalyMembershipPlan,
    policy: CensusFitPolicy,
    event_capacities: tuple[EventCapacity, ...],
    *,
    scratch: Path,
    max_role_bytes: int = 2 * 1024**3,
) -> AnomalyCensusTraining:
    """Exhaust every membership before fitting; never replace unknown with clean.

    Full eligible train/validation vectors are partitioned only by the original
    native event/currency groups. The entire validation population sets each
    threshold. At most 64 parity probes per group are retained in memory; those
    probes do not select training rows or the validation threshold population.
    All files disappear on success or failure. The enclosing attempt must keep
    the cost of failed fits as well as preparation and scoring outside fit.
    """
    plan = CampaignAnomalyMembershipPlan.model_validate_json(plan.model_dump_json())
    policy = CensusFitPolicy.model_validate_json(policy.model_dump_json())
    capacities = tuple(
        EventCapacity.model_validate_json(c.model_dump_json()) for c in event_capacities
    )
    if tuple(c.event_type for c in capacities) != ("sale_completed", "return_completed"):
        raise ValueError("campaign_anomaly_training_event_capacities")
    if type(max_role_bytes) is not int or not 4096 <= max_role_bytes <= 8 * 1024**3:
        raise ValueError("campaign_anomaly_training_role_budget")
    group_keys = sorted({(s.event_type, s.currency) for s in plan.scopes})
    keys = [
        (event, currency, role)
        for event, currency in group_keys
        for role in ("train", "validation")
    ]
    traces = {key: hashlib.sha256() for key in keys}
    counts: Counter[tuple[str, ...]] = Counter()
    sizes: Counter[tuple[str, ...]] = Counter()
    membership_traces = {role: hashlib.sha256(b"[") for role in ("train", "validation")}
    role_counts: Counter[str] = Counter()
    membership_counts: Counter[tuple[str, bool]] = Counter()
    probes: dict[tuple[str, str], list[NumericalRow]] = {key: [] for key in group_keys}
    total = observed = last_reserve_bytes = 0

    def reserve(additional: int = 0) -> None:
        if shutil.disk_usage(scratch).free < policy.minimum_free_disk_bytes + additional:
            raise SnapshotError("campaign_anomaly_training_disk_reserve")

    reserve(MAX_ROW_BYTES * 128)
    with tempfile.TemporaryDirectory(prefix="anomaly-census-roles-", dir=scratch) as tmp:
        root = Path(tmp)
        paths = {key: root / f"{index}.jsonl" for index, key in enumerate(keys)}
        expected = iter(_expected(plan))
        with ExitStack() as stack:
            streams = {key: stack.enter_context(paths[key].open("xb")) for key in keys}
            for value in rows:
                if not isinstance(value, AnomalyMembershipRow):
                    raise ValueError("campaign_anomaly_training_membership_type")
                member = Membership.model_validate_json(value.membership.model_dump_json())
                slot = next(expected, None)
                if slot != (
                    (*series_key(member), member.business_date.isoformat()),
                    member.role,
                ) or (
                    member.scoring_origin
                    != scoring_origin(member.business_date, member.event_type, Policy())
                ):
                    raise ValueError("campaign_anomaly_training_membership_order_or_clock")
                reasons: list[str] = (
                    [] if member.input_status == "ready_input" else [member.input_status]
                )
                if member.role == "gap":
                    reasons.append("split_gap")
                elif member.role == "train" and member.scoring_origin > plan.training_cutoff:
                    reasons.append("outcome_after_training_cutoff")
                elif member.role == "validation" and member.scoring_origin > plan.selection_cutoff:
                    reasons.append("outcome_after_selection_cutoff")
                if member.reason_codes != tuple(reasons):
                    raise ValueError("campaign_anomaly_training_membership_eligibility")
                eligible = member.eligible and member.role in ("train", "validation")
                numerical = value.training_or_validation_row
                if eligible != (numerical is not None) or (
                    (member.input_status == "no_declaration") != (value.public_point_sha256 is None)
                ):
                    raise ValueError("campaign_anomaly_training_vector_membership_binding")
                if value.public_point_sha256 is not None and (
                    len(value.public_point_sha256) != 64
                    or any(c not in "0123456789abcdef" for c in value.public_point_sha256)
                ):
                    raise ValueError("campaign_anomaly_training_point_digest")
                if member.role in membership_traces:
                    membership_traces[member.role].update(
                        (b"," if role_counts[member.role] else b"")
                        + canonical_json(member.model_dump(mode="json"))
                    )
                    role_counts[member.role] += 1
                membership_counts[(member.role, member.eligible)] += 1
                observed += 1
                if numerical is None:
                    continue
                numerical = CountRateRow.model_validate_json(numerical.model_dump_json())
                if numerical.event_type != member.event_type:
                    raise ValueError("campaign_anomaly_training_vector_event")
                key = (member.event_type, member.currency, member.role)
                raw = canonical_json(numerical.model_dump(mode="json")) + b"\n"
                total += len(raw)
                if (
                    total > max_role_bytes
                    or len(raw) > MAX_ROW_BYTES
                    or counts[key] >= policy.max_train_rows
                ):
                    raise SnapshotError("campaign_anomaly_training_role_budget")
                if total - last_reserve_bytes >= MAX_ROW_BYTES * 128:
                    reserve(MAX_ROW_BYTES * 128)
                    last_reserve_bytes = total
                streams[key].write(raw)
                traces[key].update(raw)
                counts[key] += 1
                sizes[key] += len(raw)
                group_key = (member.event_type, member.currency)
                if member.role == "validation" and len(probes[group_key]) < 64:
                    probes[group_key].append(numerical)
        if observed != plan.rows or next(expected, None) is not None:
            raise ValueError("campaign_anomaly_training_incomplete_membership")
        reserve()
        files = {
            key: _RoleFile(paths[key], counts[key], sizes[key], traces[key].hexdigest())
            for key in keys
        }
        groups, resources = [], []
        for event, currency in group_keys:
            train = files[(event, currency, "train")]
            validation = files[(event, currency, "validation")]
            pipeline = None
            if train.count >= policy.minimum_train_rows:
                pipeline, receipt = fit_census_pipeline(
                    train.rows(), train.count, probes[(event, currency)], policy, scratch=root
                )
                resources.append(receipt)
            else:
                # Even an under-supported group must verify its whole role file.
                for _ in train.rows():
                    pass
            capacity = next(c for c in capacities if c.event_type == event)
            local_policy = CensusFitPolicy.model_validate(
                {
                    **policy.model_dump(),
                    "validation_alert_fraction": capacity.alert_fraction,
                    "validation_high_fraction": capacity.high_fraction,
                }
            )
            reserve(max(1, validation.count) * 8)
            baseline = census_capacity_threshold(
                (baseline_score(r) for r in validation.rows()),
                validation.count,
                local_policy,
                scratch=root,
            )

            reserve(max(1, validation.count) * 8)
            forest = (
                census_capacity_threshold(
                    _forest_scores(pipeline, validation),
                    validation.count,
                    local_policy,
                    scratch=root,
                )
                if pipeline is not None
                else None
            )
            groups.append(
                CensusGroup(
                    event_type=event,
                    currency=currency,
                    training_rows=train.count,
                    pipeline=pipeline,
                    baseline_threshold=baseline,
                    forest_threshold=forest,
                )
            )
        for trace in membership_traces.values():
            trace.update(b"]")
        return AnomalyCensusTraining(
            membership_plan_sha256=canonical_sha256(plan.model_dump(mode="json")),
            training_membership_sha256=membership_traces["train"].hexdigest(),
            validation_membership_sha256=membership_traces["validation"].hexdigest(),
            requested_rows=observed,
            membership_counts=tuple(
                (role, eligible, count)
                for (role, eligible), count in sorted(membership_counts.items())
            ),
            groups=tuple(groups),
            fit_resources=tuple(resources),
            role_file_bytes=total,
        )
