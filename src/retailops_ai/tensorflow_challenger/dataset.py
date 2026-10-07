"""Group the existing typed forecast population without losing partial horizons."""

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from statistics import median, pstdev

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.comparison import require_same_forecast_keys
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.functional_preprocessing import (
    fit_ordered_train_samples,
    transform_values,
)
from retailops_ai.forecasting.manifest_contract import (
    FeaturePolicy,
    FoldPlan,
    LabelPoint,
    Membership,
)
from retailops_ai.forecasting.manifests import feature_key
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.contract import Normalization, Scale


@dataclass(frozen=True)
class Sample:
    row: InputRow
    membership: Membership
    label: LabelPoint


@dataclass(frozen=True)
class Window:
    history: HistoryContext
    samples: tuple[Sample, ...]


def population_sha256(population: Iterable[Window]) -> str:
    """Hash the existing canonical JSON array in row order without retaining all row dumps."""
    digest = hashlib.sha256(b"[")
    separator = b""
    for window in population:
        for sample in window.samples:
            digest.update(separator)
            digest.update(canonical_bytes(sample.row.model_dump(mode="json")))
            separator = b","
    digest.update(b"]")
    return digest.hexdigest()


def windows(
    records: Iterable[tuple[InputRow, Membership, LabelPoint, HistoryContext]],
    *,
    fold: FoldPlan,
    role: str,
    max_windows: int = 3000,
) -> tuple[Window, ...]:
    """Training adapter accepts only train/validation, never a holdout role."""
    if role not in {"train", "validation"}:
        raise SnapshotError("tensorflow_development_role_required")
    groups: dict[str, list[Sample]] = defaultdict(list)
    histories: dict[str, HistoryContext] = {}
    keys: list[ForecastKey] = []
    for row, member, label, history in records:
        identifier = history.content_sha256()
        key = ForecastKey.model_validate(row.model_dump(include=set(ForecastKey.model_fields)))
        if (
            feature_key(row) != feature_key(member)
            or feature_key(row) != feature_key(label)
            or member.fold != fold.name
            or label.fold != fold.name
            or member.role != role
            or label.role != role
            or fold.role(row.forecast_origin.date()) != role
            or label.knowledge_cutoff != fold.label_cutoff(member.role)
            or row.history_context_sha256 != identifier
            or (row.product_id, row.selling_location_id, row.channel, row.forecast_origin)
            != (
                history.product_id,
                history.selling_location_id,
                history.channel,
                history.forecast_origin,
            )
            or member.label_content_sha256 != canonical_sha256(label.model_dump(mode="json"))
        ):
            raise SnapshotError("tensorflow_sample_binding_mismatch")
        if member.eligible and (label.status != "eligible" or label.observed_sales_units is None):
            raise SnapshotError("tensorflow_requires_mature_eligible_labels")
        if row.history_active_days != len(history.points) or row.history_known_days != sum(
            p.status != "missing" for p in history.points
        ):
            raise SnapshotError("tensorflow_history_statistics_mismatch")
        keys.append(key)
        groups[identifier].append(Sample(row, member, label))
        histories[identifier] = history
        if len(groups) > max_windows or len(keys) > max_windows * 14:
            raise SnapshotError("tensorflow_development_window_budget")
    if not keys:
        raise SnapshotError("tensorflow_empty_population")
    # Also detects duplicate horizons before any fitting or grouping can hide them.
    require_same_forecast_keys(keys, keys)
    result = tuple(
        Window(histories[k], tuple(sorted(v, key=lambda s: s.row.horizon_days)))
        for k, v in sorted(groups.items(), key=lambda item: feature_key(item[1][0].row))
    )
    require_same_forecast_keys(keys, [s.row for w in result for s in w.samples])
    return result


def _scale(numbers: list[float]) -> Scale:
    return Scale(center=math.fsum(numbers) / len(numbers), spread=pstdev(numbers) or 1.0)


def fit_normalization(
    train: tuple[Window, ...], *, fold: FoldPlan, feature_set_id: str, split_id: str
) -> Normalization:
    if any(
        s.membership.role != "train"
        or s.membership.fold != fold.name
        or fold.role(s.row.forecast_origin.date()) != "train"
        for w in train
        for s in w.samples
    ):
        raise SnapshotError("tensorflow_normalization_eligible_fold_train_only")
    eligible = sorted(
        (s for w in train for s in w.samples if s.membership.eligible),
        key=lambda s: feature_key(s.row),
    )
    # The existing fitter checks eligibility, chronology, full keys and fold role.
    encoding = fit_ordered_train_samples(
        ((s.row, s.membership) for s in eligible),
        fold=fold,
        policy=FeaturePolicy(),
        feature_set_id=feature_set_id,
        split_id=split_id,
    )
    # Welford keeps scaling memory proportional to the encoded width, not all rows.
    centers = [0.0] * len(encoding.descriptor.output_columns)
    moments = [0.0] * len(centers)
    for count, sample in enumerate(eligible, 1):
        vector = transform_values({v.name: v.value for v in sample.row.values}, encoding)
        for index, number in enumerate(vector):
            delta = number - centers[index]
            centers[index] += delta / count
            moments[index] += delta * (number - centers[index])
    scales = tuple(
        Scale(center=center, spread=math.sqrt(max(0.0, moment) / len(eligible)) or 1.0)
        for center, moment in zip(centers, moments, strict=True)
    )
    history_numbers = [
        float(p.observed_units)
        for w in train
        if any(s.membership.eligible for s in w.samples)
        for p in w.history.points
        if p.observed_units is not None
    ]
    if not history_numbers:
        history_numbers = [0.0]
    targets = [
        float(s.label.observed_sales_units)
        for s in eligible
        if s.label.observed_sales_units is not None
    ]
    digest = hashlib.sha256()
    for window in train:
        digest.update(
            canonical_bytes(
                {
                    "history": window.history.model_dump(mode="json"),
                    "samples": [
                        {
                            "row": s.row.model_dump(mode="json"),
                            "member": s.membership.model_dump(mode="json"),
                            "label": s.label.model_dump(mode="json"),
                        }
                        for s in window.samples
                    ],
                }
            )
            + b"\n"
        )
    return Normalization(
        encoding=encoding,
        scales=scales,
        history_fill=float(median(history_numbers)),
        history_scale=_scale(history_numbers),
        target_scale=max(1.0, math.fsum(targets) / len(targets)),
        train_windows_sha256=digest.hexdigest(),
    )


def transform_window(window: Window, state: Normalization) -> tuple[float, ...]:
    by_day = {p.business_date: p for p in window.history.points}
    output: list[float] = []
    for offset in range(27, -1, -1):
        p = by_day.get(window.history.forecast_origin.date() - timedelta(days=offset))
        units = (
            state.history_fill if p is None or p.observed_units is None else float(p.observed_units)
        )
        output.extend(
            (
                (units - state.history_scale.center) / state.history_scale.spread,
                float(p is not None and p.observed_units is None),
                float(p is None),
            )
        )
    by_horizon = {s.row.horizon_days: s.row for s in window.samples}
    for horizon in range(1, 15):
        row = by_horizon.get(horizon)
        if row is None:
            output.extend([0.0] * len(state.scales) + [0.0])
        else:
            vector = transform_values({v.name: v.value for v in row.values}, state.encoding)
            output.extend(
                (v - scale.center) / scale.spread
                for v, scale in zip(vector, state.scales, strict=True)
            )
            output.append(1.0)
    if len(output) != state.input_width or any(not math.isfinite(v) for v in output):
        raise SnapshotError("tensorflow_nonfinite_or_invalid_input")
    return tuple(output)


def targets(window: Window, state: Normalization) -> tuple[tuple[float, ...], tuple[float, ...]]:
    values, masks = [0.0] * 14, [0.0] * 14
    for sample in window.samples:
        if sample.membership.eligible:
            if sample.label.observed_sales_units is None:
                raise SnapshotError("tensorflow_missing_eligible_target")
            index = sample.row.horizon_days - 1
            values[index] = sample.label.observed_sales_units / state.target_scale
            masks[index] = 1.0
    return tuple(values), tuple(masks)
