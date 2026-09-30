"""Auditable train-only fitting; no estimators, outcomes or global encoders."""

from __future__ import annotations

import hashlib
import math
import tempfile
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FeatureID, Sha256, SplitID, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.features_contract import FEATURE_TYPES, InputRow
from retailops_ai.forecasting.manifest_contract import (
    CodePin,
    FeaturePolicy,
    FoldPlan,
    Membership,
    PreprocessingRecipe,
)
from retailops_ai.forecasting.manifest_io import code_pin, dump, iter_table
from retailops_ai.forecasting.manifests import feature_key, input_models, verify_feature_set
from retailops_ai.forecasting.splits import verify_split
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    inventory,
    read_bytes,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

MAX_TRAIN_ROWS = 50000
MAX_TRAIN_BYTES = 128 * 1024**2


class NumericFill(Contract):
    name: str
    value: float
    known_count: Annotated[int, Field(ge=0)]
    reason: Literal["train_median", "train_mode", "entirely_missing_constant_zero"]


class Vocabulary(Contract):
    name: str
    categories: tuple[str, ...] = Field(max_length=1000)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.categories != tuple(sorted(set(self.categories))):
            raise ValueError("forecast_preprocessing_vocabulary_not_unique_sorted")
        return self


class FittedDescriptor(Contract):
    version: Literal["forecast-preprocessing-1.0.0"] = "forecast-preprocessing-1.0.0"
    feature_set_id: FeatureID
    split_id: SplitID
    fold: FoldPlan
    policy: FeaturePolicy
    recipe: PreprocessingRecipe
    code: CodePin
    train_content_sha256: Sha256
    train_rows: Annotated[int, Field(ge=1, le=MAX_TRAIN_ROWS)]
    numeric: tuple[NumericFill, ...]
    categorical: tuple[Vocabulary, ...]
    output_columns: tuple[str, ...] = Field(max_length=4096)

    @model_validator(mode="after")
    def fitted_schema(self) -> Self:
        if (
            self.recipe != self.policy.preprocessing
            or tuple(n.name for n in self.numeric)
            != tuple(c for c in self.policy.columns if FEATURE_TYPES[c] != "str")
            or tuple(v.name for v in self.categorical)
            != tuple(c for c in self.policy.columns if FEATURE_TYPES[c] == "str")
        ):
            raise ValueError("forecast_preprocessing_fitted_schema_mismatch")
        expected = [x for n in self.numeric for x in (n.name, n.name + "__missing")]
        expected.extend(
            x
            for v in self.categorical
            for x in (
                v.name + "__missing",
                v.name + "__unknown",
                *(v.name + f"__category_{i}" for i in range(len(v.categories))),
            )
        )
        if tuple(expected) != self.output_columns:
            raise ValueError("forecast_preprocessing_output_columns_mismatch")
        if any(
            n.known_count > self.train_rows
            or ((n.known_count == 0) != (n.reason == "entirely_missing_constant_zero"))
            or (n.known_count == 0 and n.value != 0)
            for n in self.numeric
        ):
            raise ValueError("forecast_preprocessing_fill_count_mismatch")
        return self


class FittedState(Contract):
    preprocessing_id: Annotated[str, Field(pattern=r"^forecast-preprocessing-sha256-[0-9a-f]{64}$")]
    descriptor: FittedDescriptor
    generated_at: UtcTime

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.preprocessing_id != "forecast-preprocessing-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ):
            raise ValueError("forecast_preprocessing_identity_mismatch")
        return self


def fit_train_samples(
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
    content: dict[bytes, bytes] = {}
    size = 0
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
        size += len(body)
        row_key = feature_key(row)
        if row_key in content:
            raise SnapshotError("duplicate_forecast_preprocessing_train_row")
        content[row_key] = body
        if len(content) > MAX_TRAIN_ROWS or size > MAX_TRAIN_BYTES:
            raise SnapshotError("forecast_preprocessing_training_resource_limit")
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
    if not content:
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
    digest = hashlib.sha256()
    for row_key in sorted(content):
        digest.update(content[row_key] + b"\n")
    descriptor = FittedDescriptor(
        feature_set_id=feature_set_id,
        split_id=split_id,
        fold=fold,
        policy=policy,
        recipe=policy.preprocessing,
        code=code_pin(),
        train_content_sha256=digest.hexdigest(),
        train_rows=len(content),
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


def transform(row: InputRow, state: FittedState, *, feature_set_id: str) -> tuple[float, ...]:
    state = FittedState.model_validate_json(state.model_dump_json())
    if feature_set_id != state.descriptor.feature_set_id:
        raise SnapshotError("forecast_preprocessing_feature_set_mismatch")
    return _transform_values(row, state)


def transform_inference(
    row: InputRow, state: FittedState, *, policy: FeaturePolicy
) -> tuple[float, ...]:
    """New inference data can share a recipe; fitted medians/vocabulary remain unchanged."""
    state = FittedState.model_validate_json(state.model_dump_json())
    policy = FeaturePolicy.model_validate_json(policy.model_dump_json())
    if policy != state.descriptor.policy:
        raise SnapshotError("forecast_inference_feature_policy_mismatch")
    return _transform_values(InputRow.model_validate_json(row.model_dump_json()), state)


def _transform_values(row: InputRow, state: FittedState) -> tuple[float, ...]:
    values = {v.name: v.value for v in row.values}
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
    if any(not math.isfinite(v) for v in output):
        raise SnapshotError("forecast_preprocessing_nonfinite_output")
    return tuple(output)


def fitted_state_for_fold(feature_dir: Path, split_dir: Path, fold_name: str) -> FittedState:
    split = verify_split(split_dir, feature_dir)
    features = verify_feature_set(feature_dir)
    if split.descriptor.qualification_status != "passed":
        raise SnapshotError("forecast_preprocessing_split_not_ready")
    fold = next((f for f in split.descriptor.resolved_policy.folds if f.name == fold_name), None)
    if fold is None:
        raise SnapshotError("forecast_preprocessing_unknown_fold")
    train_keys: dict[bytes, Membership] = {}
    for membership in iter_table(split_dir, "memberships", split.tables["memberships"], Counter()):
        if (
            isinstance(membership, Membership)
            and membership.fold == fold_name
            and membership.role == "train"
            and membership.eligible
        ):
            train_keys[feature_key(membership)] = membership
            if len(train_keys) > MAX_TRAIN_ROWS:
                raise SnapshotError("forecast_preprocessing_training_resource_limit")

    def samples() -> Iterable[tuple[InputRow, Membership]]:
        for row in input_models(feature_dir, "features"):
            if (
                isinstance(row, InputRow)
                and (membership := train_keys.pop(feature_key(row), None)) is not None
            ):
                yield row, membership
        if train_keys:
            raise SnapshotError("forecast_preprocessing_missing_train_features")

    return fit_train_samples(
        samples(),
        fold=fold,
        policy=features.descriptor.resolved_policy,
        feature_set_id=features.feature_set_id,
        split_id=split.split_id,
    )


def fit_fold(feature_dir: Path, split_dir: Path, fold_name: str, output_root: Path) -> Path:
    output_root = output_root.absolute()
    if any(output_root.is_relative_to(p.absolute()) for p in (feature_dir, split_dir)):
        raise SnapshotError("forecast_preprocessing_output_inside_input")
    state = fitted_state_for_fold(feature_dir, split_dir, fold_name)
    output_root.mkdir(parents=True, exist_ok=True)
    checked_directory(output_root)
    with tempfile.TemporaryDirectory(prefix=".preprocessing-", dir=output_root) as temporary:
        root = Path(temporary)
        dump(root / "preprocessing.json", state)
        load_preprocessing(root)
        fsync_tree(root)
        destination = output_root / state.preprocessing_id
        try:
            publish_noreplace(root, destination)
        except FileExistsError:
            if load_preprocessing(destination).descriptor != state.descriptor:
                raise SnapshotError("forecast_preprocessing_publication_conflict") from None
        return destination


def load_preprocessing(root: Path) -> FittedState:
    checked_directory(root)
    raw = read_bytes(root, "preprocessing.json")
    decode_json(raw)
    state = FittedState.model_validate_json(raw)
    inventory(root, {"preprocessing.json"})
    return state


def verify_preprocessing(root: Path, feature_dir: Path, split_dir: Path) -> FittedState:
    state = load_preprocessing(root)
    expected = fitted_state_for_fold(feature_dir, split_dir, state.descriptor.fold.name)
    if state.descriptor != expected.descriptor:
        raise SnapshotError("forecast_preprocessing_train_fit_mismatch")
    return state
