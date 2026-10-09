"""Complete development memberships and causal count-rate rows, one series at a time.

This numerical adapter grants no Source read, Project fit or final permission.
Its enclosing public-parent context and campaign operation must both complete.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.anomaly_detectors.protocol import (
    Membership,
    Scope,
    Window,
    point_key,
    requested,
    series_key,
)
from retailops_ai.anomaly_detectors.rows import CountRateRow
from retailops_ai.anomaly_portfolio.model import count_rate_row
from retailops_ai.anomaly_portfolio.protocol import PortfolioProtocol
from retailops_ai.data_contracts.common import Contract, Sha256, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.qualified_anomalies.contract import Point


class CampaignAnomalyMembershipPlan(Contract):
    version: Literal["ai09-anomaly-membership-census-1.0.0"] = (
        "ai09-anomaly-membership-census-1.0.0"
    )
    phase: Literal["development"] = "development"
    feature_plan_sha256: Sha256
    native_points_sha256: Sha256
    scopes: Annotated[tuple[Scope, ...], Field(min_length=1, max_length=65536)]
    train: Window
    validation: Window
    test: Window
    training_cutoff: UtcTime
    selection_cutoff: UtcTime
    max_requested_rows: Annotated[int, Field(ge=1, le=20000000)]
    max_series_bytes: Annotated[int, Field(ge=4096, le=256 * 1024**2)] = 64 * 1024**2
    history_days: Literal[6] = 6
    population: Literal["all_declared_series_days_including_unknown_and_gaps"] = (
        "all_declared_series_days_including_unknown_and_gaps"
    )

    @property
    def rows(self) -> int:
        return len(self.scopes) * ((self.test.end - self.train.start).days + 1)

    def native_protocol(self, scope: Scope) -> PortfolioProtocol:
        return PortfolioProtocol(
            scopes=(scope,),
            train=self.train,
            validation=self.validation,
            test=self.test,
            training_cutoff=self.training_cutoff,
            selection_cutoff=self.selection_cutoff,
        )

    @model_validator(mode="after")
    def causal_population(self) -> Self:
        keys = [series_key(s) for s in self.scopes]
        if keys != sorted(set(keys)) or self.rows > self.max_requested_rows:
            raise ValueError("campaign_anomaly_membership_scope_or_budget")
        # Native causal rules are event-clock dependent, not product dependent.
        # Validate each actual event clock without applying the old total-row cap
        # to this full census; each series is still a complete native protocol.
        for event in {s.event_type for s in self.scopes}:
            self.native_protocol(next(s for s in self.scopes if s.event_type == event))
        return self


@dataclass(frozen=True)
class AnomalyMembershipRow:
    membership: Membership
    training_or_validation_row: CountRateRow | None
    public_point_sha256: str | None


def iter_anomaly_membership_census(
    points: Iterable[Point],
    plan: CampaignAnomalyMembershipPlan,
) -> Iterator[AnomalyMembershipRow]:
    """Use unchanged native membership and feature math for every requested day.

    Points are ordered by full native series/date and restricted to the declared
    development interval plus six causal history days. Missing declarations
    remain explicit unknown memberships. Gap, test and cutoff-excluded rows
    never yield a training/validation vector. Late invalid input rejects the
    operation; callers must exhaust the iterator before accepting its result.
    """
    plan = CampaignAnomalyMembershipPlan.model_validate_json(plan.model_dump_json())
    allowed = {series_key(s) for s in plan.scopes}
    earliest = plan.train.start - timedelta(days=plan.history_days)
    iterator = iter(points)
    end = object()
    previous: tuple[str, ...] | None = None

    def advance() -> Point | None:
        nonlocal previous
        value = next(iterator, end)
        if value is end:
            return None
        if not isinstance(value, Point):
            raise ValueError("campaign_anomaly_membership_point_type")
        value = Point.model_validate_json(value.model_dump_json())
        key = point_key(value)
        if (
            previous is not None
            and key <= previous
            or series_key(value) not in allowed
            or not earliest <= value.business_date <= plan.test.end
        ):
            raise ValueError("campaign_anomaly_membership_point_order_scope_or_window")
        previous = key
        return value

    pending = advance()
    emitted = 0
    for scope in plan.scopes:
        key = series_key(scope)
        series: list[Point] = []
        series_bytes = 0
        while pending is not None and series_key(pending) == key:
            series_bytes += len(pending.model_dump_json().encode())
            if series_bytes > plan.max_series_bytes:
                raise ValueError("campaign_anomaly_membership_series_byte_budget")
            series.append(pending)
            pending = advance()
        # A validated protocol is at most 2001 days; history adds six points.
        if len(series) > 2007:
            raise ValueError("campaign_anomaly_membership_series_budget")
        native = plan.native_protocol(scope)
        indexed: dict[tuple[object, ...], Point] = {
            (*series_key(p), p.business_date): p for p in series
        }
        for membership, point in requested(native, series):
            numerical = (
                count_rate_row(point, indexed)
                if membership.eligible
                and membership.role in ("train", "validation")
                and point is not None
                else None
            )
            if (
                membership.eligible
                and membership.role in ("train", "validation")
                and numerical is None
            ):
                raise ValueError("campaign_anomaly_membership_eligible_vector_missing")
            emitted += 1
            yield AnomalyMembershipRow(
                membership=membership,
                training_or_validation_row=numerical,
                public_point_sha256=canonical_sha256(point.model_dump(mode="json"))
                if point is not None
                else None,
            )
    if pending is not None or emitted != plan.rows:
        raise ValueError("campaign_anomaly_membership_incomplete_census")
