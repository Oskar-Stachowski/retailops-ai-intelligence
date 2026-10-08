"""Bounded native anomaly scoring of a complete, explicitly requested census.

This is a numerical worker primitive, not campaign authorization or quality
evidence. Its caller must verify the public feature parent and journal before
providing points, exhaust the iterator and audit the completed artifact. No
source, truth, model fitting, final permission or lifecycle state is read here.
The existing native scorer, its limits and its cutoff semantics are unchanged.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from retailops_ai.anomaly_detectors.contract import Family
from retailops_ai.anomaly_detectors.protocol import Scope, Window, point_key, series_key
from retailops_ai.anomaly_detectors.rows import NumericalRow
from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.anomaly_evaluation.verification import verify_scores
from retailops_ai.anomaly_portfolio.model import Model, score, scoring_row
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.qualified_anomalies.contract import Point

MAX_NATIVE_POINTS = 10000
MAX_REQUESTED_ROWS = 20000000
MAX_SCOPES = 65536
HISTORY_DAYS = 6
_END = object()


@dataclass(frozen=True)
class AnomalyScoredRow:
    decision: Decision
    model_row: NumericalRow | None
    public_point_sha256: str | None


def iter_anomaly_census_scores(
    model: Model,
    points: Iterable[Point],
    scopes: tuple[Scope, ...],
    window: Window,
    family: Family,
    role: Literal["validation", "final_test", "batch"],
    as_of: datetime,
    *,
    batch_points: int = 8192,
    max_requested_rows: int = MAX_REQUESTED_ROWS,
) -> Iterator[AnomalyScoredRow]:
    """Score every scope/day without materializing the whole feature census.

    Points must be strictly ordered by the native series key and date, belong
    to the requested scopes, and cover only the requested window plus six prior
    days. Missing declarations remain native abstentions. One batch contains
    whole series, including their history, so a partition cannot change causal
    multiscale/count-rate features. Both public points and the saved model are
    revalidated; each emitted batch is independently replayed by verify_scores.
    A malformed later point fails the operation rather than qualifying an
    earlier partial output. The caller must exhaust this iterator.
    """
    if not 1 <= len(scopes) <= MAX_SCOPES:
        raise ValueError("campaign_anomaly_scoring_scope_budget")
    model = Model.model_validate_json(model.model_dump_json())
    window = Window.model_validate_json(window.model_dump_json())
    scopes = tuple(Scope.model_validate_json(s.model_dump_json()) for s in scopes)
    keys = [series_key(s) for s in scopes]
    days = (window.end - window.start).days + 1
    if (
        not keys
        or keys != sorted(set(keys))
        or days > 2001
        or family not in ("seasonal_residual", "isolation_forest")
        or role not in ("validation", "final_test", "batch")
        or as_of.utcoffset() != timedelta(0)
    ):
        raise ValueError("campaign_anomaly_scoring_scope_window_or_clock")
    if (
        type(batch_points) is not int
        or not days + HISTORY_DAYS <= batch_points <= MAX_NATIVE_POINTS
        or type(max_requested_rows) is not int
        or not 1 <= max_requested_rows <= MAX_REQUESTED_ROWS
        or len(keys) * days > max_requested_rows
    ):
        raise ValueError("campaign_anomaly_scoring_budget")
    allowed = set(keys)
    earliest = window.start - timedelta(days=HISTORY_DAYS)
    source = iter(points)
    previous: tuple[str, ...] | None = None

    def next_point() -> Point | None:
        nonlocal previous
        value = next(source, _END)
        if value is _END:
            return None
        if not isinstance(value, Point):
            raise ValueError("campaign_anomaly_scoring_point_type")
        value = Point.model_validate_json(value.model_dump_json())
        key = point_key(value)
        if previous is not None and key <= previous:
            raise ValueError("campaign_anomaly_scoring_point_order_or_duplicate")
        if series_key(value) not in allowed or not earliest <= value.business_date <= window.end:
            raise ValueError("campaign_anomaly_scoring_point_scope_or_window")
        previous = key
        return value

    pending = next_point()
    scopes_per_batch = batch_points // (days + HISTORY_DAYS)
    for offset in range(0, len(scopes), scopes_per_batch):
        selected_scopes = scopes[offset : offset + scopes_per_batch]
        last_key = series_key(selected_scopes[-1])
        batch: list[Point] = []
        while pending is not None and series_key(pending) <= last_key:
            batch.append(pending)
            pending = next_point()
        # Whole series and unique in-window dates bound the number of features
        # independently of whether declarations are missing from the parent.
        if len(batch) > batch_points:
            raise ValueError("campaign_anomaly_scoring_native_batch_budget")
        decisions = score(model, batch, selected_scopes, window, family, role, as_of)
        indexed: dict[tuple[object, ...], Point] = {
            (*series_key(p), p.business_date): p for p in batch
        }
        rows: list[NumericalRow | None] = []
        hashes: list[str | None] = []
        for decision in decisions:
            public_point = indexed.get((*series_key(decision), decision.business_date))
            rows.append(
                scoring_row(model, public_point, indexed) if public_point is not None else None
            )
            hashes.append(
                canonical_sha256(public_point.model_dump(mode="json"))
                if public_point is not None
                else None
            )
        verify_scores(
            model,
            decisions,
            [r.model_dump(mode="json") if r is not None else None for r in rows],
        )
        for decision, row, digest in zip(decisions, rows, hashes, strict=True):
            yield AnomalyScoredRow(decision, row, digest)
        # Drop this batch before reading the next complete group of series.
        # A yielded row contains scalars/model features, never the Point parent.
        del batch, decisions, indexed, rows, hashes, public_point
    if pending is not None:
        raise ValueError("campaign_anomaly_scoring_unconsumed_point")
