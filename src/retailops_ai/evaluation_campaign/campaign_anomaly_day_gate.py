"""Native causal DayGate over complete disk days/facts and global quarantine.

Actual source ancestry, producer closure and journal permission remain separate
requirements. Inner gate results never claim an encompassing campaign is ready.
"""

from collections.abc import Iterator, Mapping
from copy import deepcopy
from typing import Annotated, Any, Literal, Self

from pydantic import Field

from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.day_qualification.contract import Point
from retailops_ai.day_qualification.gate import DayGate
from retailops_ai.evaluation_campaign.campaign_anomaly_days import CampaignAnomalyDayProjection
from retailops_ai.evaluation_campaign.campaign_anomaly_replay import CampaignAnomalyDiskReplay
from retailops_ai.full_raw_dq.source import Key
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json

Row = dict[str, Any]


class CampaignAnomalyDayGatePlan(Contract):
    version: Literal["ai09-native-disk-day-gate-plan-1.0.0"] = (
        "ai09-native-disk-day-gate-plan-1.0.0"
    )
    day_plan_sha256: Sha256
    native_days_sha256: Sha256
    replay_plan_sha256: Sha256
    accepted_facts: Annotated[int, Field(ge=0, le=20000000)]
    quarantined_records: Annotated[int, Field(ge=0, le=100000000)]
    population: Literal["all_declared_days_all_accepted_facts_global_unattributed_quarantine"] = (
        "all_declared_days_all_accepted_facts_global_unattributed_quarantine"
    )


class _AcceptedFacts(Mapping[Key, Row]):
    def __init__(self, owner: "CampaignAnomalyDiskDayGate") -> None:
        self.owner = owner

    def __len__(self) -> int:
        self.owner._live()
        return self.owner.plan.accepted_facts

    def __iter__(self) -> Iterator[Key]:
        self.owner._live()
        for fact in self.owner.replay.rows("facts"):
            self.owner._live()
            yield fact["event_type"], fact["business_id"]

    def __getitem__(self, key: Key) -> Row:
        self.owner._live()
        hash(key)
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not all(isinstance(part, str) for part in key)
        ):
            raise KeyError(key)
        fact = self.owner.replay.fact(*key)
        if fact is None:
            raise KeyError(key)
        return fact


class CampaignAnomalyDiskDayGate(DayGate):
    """Delegate every point to native DayGate.point without copying whole parents.

    One quarantine record at a time goes through the native attribution code.
    Its minimum unattributed receipt time exactly preserves native any(t<=cutoff).
    Every required accepted fact is loaded by its full native business key.
    Enclosing day/replay/public-parent contexts must subsequently finish too.
    """

    def __init__(
        self,
        days: CampaignAnomalyDayProjection,
        replay: CampaignAnomalyDiskReplay,
        plan: CampaignAnomalyDayGatePlan,
    ) -> None:
        self.projection, self.replay = days, replay
        self.plan = CampaignAnomalyDayGatePlan.model_validate_json(plan.model_dump_json())
        self._used = self._failed = self._complete = self._opened = False
        self._receipt: Row = {}
        self.queried_points = 0

    def _live(self) -> None:
        if not self._opened or self._failed:
            raise RuntimeError("campaign_anomaly_day_gate_state_unavailable")
        self.projection._db()
        self.replay._db(outputs=True)

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("campaign_anomaly_day_gate_single_use")
        self._used = True
        try:
            self.projection._db()
            self.replay._db(outputs=True)
            if (
                self.replay.parent is not self.projection.parent.parent
                or canonical_sha256(self.projection.plan.model_dump(mode="json"))
                != self.plan.day_plan_sha256
                or self.projection.native_days_sha256 != self.plan.native_days_sha256
                or canonical_sha256(self.replay.plan.model_dump(mode="json"))
                != self.plan.replay_plan_sha256
                or self.replay._db(outputs=True).execute("SELECT COUNT(*) FROM facts").fetchone()[0]
                != self.plan.accepted_facts
            ):
                raise SnapshotError("campaign_anomaly_day_gate_complete_parent_binding")
            self.replay.verify_outputs()
            self.projection.parent._replay.check_parents()
            earliest: str | None = None
            count = unattributed = 0
            for quarantined, record in self.replay.quarantined_captures():
                # The unchanged native constructor sees just this one exact
                # checked record, while parent membership remains global.
                classified = DayGate(
                    [],
                    {"accepted_facts": [], "quarantine": [quarantined]},
                    canonical_json(record) + b"\n",
                    self.projection.parent.parent,
                )
                count += 1
                if classified.unattributed:
                    received = classified.unattributed[0]
                    unattributed += 1
                    if earliest is None or stamp(received) < stamp(earliest):
                        earliest = received
            if count != self.plan.quarantined_records:
                raise SnapshotError("campaign_anomaly_day_gate_quarantine_extent_binding")
            self.quarantined_records, self.unattributed_records = count, unattributed
            self.days = self.projection.days  # type: ignore[assignment]
            self.accepted = _AcceptedFacts(self)  # type: ignore[assignment]
            self.unattributed = [] if earliest is None else [earliest]
            self._opened = True
        except BaseException:
            self._failed = True
            raise
        return self

    def point(self, grain: tuple[Any, ...], as_of: str) -> Point:
        self._live()
        result = super().point(grain, as_of)
        self.queried_points += 1
        return result

    def __exit__(self, *args: Any) -> None:
        try:
            if args[0] is None:
                self._live()
                self.replay.verify_outputs()
                if self.projection._hash_days() != self.projection.native_days_sha256:
                    raise RuntimeError("campaign_anomaly_day_gate_private_days_changed")
                self.projection.parent._replay.check_parents()
                self._receipt = {
                    "version": "ai09-native-disk-day-gate-receipt-1.0.0",
                    "plan_sha256": canonical_sha256(self.plan.model_dump(mode="json")),
                    "plan": self.plan.model_dump(mode="json"),
                    "queried_points": self.queried_points,
                    "quarantined_records": self.quarantined_records,
                    "unattributed_records": self.unattributed_records,
                    "earliest_unattributed_received_at": self.unattributed[0]
                    if self.unattributed
                    else None,
                    "native_causal_day_queries_verified": True,
                    "outer_day_replay_and_public_parent_completion_required": True,
                    "producer_closure_policy_verified": False,
                    "source_generation_ancestry_verified": False,
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
            self._opened = False

    def receipt(self) -> Row:
        if not self._complete or self._failed:
            raise SnapshotError("campaign_anomaly_day_gate_not_completed")
        return deepcopy(self._receipt)
