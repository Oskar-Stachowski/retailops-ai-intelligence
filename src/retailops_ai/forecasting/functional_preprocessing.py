"""Same train-only encodings, ordered streaming hashes instead of retaining provenance bodies."""

import hashlib
import json
import math
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib.resources import files
from statistics import median
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.features_contract import FEATURE_TYPES, InputRow
from retailops_ai.forecasting.manifest_contract import CodePin, FeaturePolicy, FoldPlan, Membership
from retailops_ai.forecasting.manifest_io import code_pin
from retailops_ai.forecasting.manifests import feature_key
from retailops_ai.forecasting.preprocessing import (
    MAX_TRAIN_BYTES,
    MAX_TRAIN_ROWS,
    FittedDescriptor,
    FittedState,
    NumericFill,
    Vocabulary,
)
from retailops_ai.source_snapshot.files import SnapshotError


def streaming_code() -> CodePin:
    pin = code_pin().model_dump(mode="json")
    pin["code_files"]["forecasting/functional_preprocessing.py"] = hashlib.sha256(
        files("retailops_ai.forecasting").joinpath("functional_preprocessing.py").read_bytes()
    ).hexdigest()
    pin["code_sha256"] = canonical_sha256(pin["code_files"])
    return CodePin.model_validate_json(json.dumps(pin))


def fit_ordered_train_samples(
    samples: Iterable[tuple[InputRow, Membership]],
    *,
    fold: FoldPlan,
    policy: FeaturePolicy,
    feature_set_id: str,
    split_id: str,
) -> FittedState:
    """Internal fitting primitive; the directory API additionally verifies both pinned artifacts."""
    numeric: dict[str, list[float]] = {c: [] for c in policy.columns if FEATURE_TYPES[c] != "str"}
    categories: dict[str, set[str]] = {
        c: set() for c in policy.columns if FEATURE_TYPES[c] == "str"
    }
    previous: bytes | None = None
    count = 0
    digest = hashlib.sha256()
    for row, membership in samples:
        if (
            membership.fold != fold.name
            or membership.role != "train"
            or not membership.eligible
            or fold.role(row.forecast_origin.date()) != "train"
            or feature_key(membership) != feature_key(row)
            or row.forecast_origin > fold.training_cutoff
        ):
            raise SnapshotError("forecast_preprocessing_requires_eligible_fold_train_only")
        body = canonical_bytes(
            {"input": row.model_dump(mode="json"), "membership": membership.model_dump(mode="json")}
        )
        row_key = feature_key(row)
        if previous is not None and row_key <= previous:
            raise SnapshotError("functional_preprocessing_requires_unique_sorted_train_rows")
        previous = row_key
        count += 1
        if count > MAX_TRAIN_ROWS or len(body) > 2 * 1024**2:
            raise SnapshotError("forecast_preprocessing_training_resource_limit")
        digest.update(body + b"\n")
        values = {v.name: v.value for v in row.values}
        for name in numeric:
            value = values[name]
            if value is not None:
                number = float(value)
                if not math.isfinite(number):
                    raise SnapshotError("forecast_preprocessing_nonfinite_number")
                numeric[name].append(number)
        for name in categories:
            if values[name] is not None:
                categories[name].add(str(values[name]))
                if len(categories[name]) > 1000:
                    raise SnapshotError("forecast_preprocessing_vocabulary_limit")
        if count % 256 == 0:
            retained = sum(sys.getsizeof(v) + len(v) * sys.getsizeof(0.0) for v in numeric.values())
            retained += sum(
                sys.getsizeof(v) + sum(sys.getsizeof(x) for x in v) for v in categories.values()
            )
            if retained > MAX_TRAIN_BYTES:
                raise SnapshotError("forecast_preprocessing_training_resource_limit")
    if not count:
        raise SnapshotError("forecast_preprocessing_no_eligible_train_rows")
    fills = tuple(
        NumericFill(
            name=name,
            value=0.0
            if not numbers
            else float(sum(numbers) > len(numbers) / 2)
            if FEATURE_TYPES[name] == "bool"
            else float(median(numbers)),
            known_count=len(numbers),
            reason="entirely_missing_constant_zero"
            if not numbers
            else "train_mode"
            if FEATURE_TYPES[name] == "bool"
            else "train_median",
        )
        for name, numbers in numeric.items()
    )
    vocabulary = tuple(
        Vocabulary(name=name, categories=tuple(sorted(values)))
        for name, values in categories.items()
    )
    columns = [x for n in fills for x in (n.name, n.name + "__missing")]
    columns.extend(
        x
        for v in vocabulary
        for x in (
            v.name + "__missing",
            v.name + "__unknown",
            *(v.name + f"__category_{i}" for i in range(len(v.categories))),
        )
    )
    descriptor = FittedDescriptor(
        feature_set_id=feature_set_id,
        split_id=split_id,
        fold=fold,
        policy=policy,
        recipe=policy.preprocessing,
        code=streaming_code(),
        train_content_sha256=digest.hexdigest(),
        train_rows=count,
        numeric=fills,
        categorical=vocabulary,
        output_columns=tuple(columns),
    )
    return FittedState(
        preprocessing_id="forecast-preprocessing-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
        generated_at=datetime.now(UTC),
    )


def transform_values(values: dict[str, Any], state: FittedState) -> tuple[float, ...]:
    """Internal transform for an already validated state and typed feature row."""
    output: list[float] = []
    for fill in state.descriptor.numeric:
        value = values[fill.name]
        output.extend((fill.value if value is None else float(value), float(value is None)))
    for vocabulary in state.descriptor.categorical:
        value = values[vocabulary.name]
        output.extend(
            (float(value is None), float(value is not None and value not in vocabulary.categories))
        )
        output.extend(float(value == category) for category in vocabulary.categories)
    if any(not math.isfinite(value) for value in output):
        raise SnapshotError("functional_nonfinite_transformed_input")
    return tuple(output)
