"""Canonical JSON v1, logical row hashing and separate content/transport identity."""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import (
    Classification,
    Contract,
    CuratedID,
    FeatureID,
    LabelID,
    ModelID,
    NonNegativeInt,
    Provenance,
    RunID,
    Sha256,
    SourceID,
    SplitID,
    Symbol,
)

Role = Literal["source", "curated", "features", "labels", "split", "predictions"]


def _finite(value: object) -> None:
    # Canonical payloads overwhelmingly contain builtin scalars and containers.
    # Keep subclasses and custom mappings on the original validation path.
    kind = type(value)
    if kind is str or kind is int or kind is bool or value is None:
        return
    if type(value) is list or type(value) is tuple:
        for item in value:
            _finite(item)
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite_json_number")
    if type(value) is dict or isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("json_keys_must_be_strings")
        for item in value.values():
            _finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite(item)


def canonical_bytes(value: object) -> bytes:
    _finite(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def logical_rows_sha256(rows: Sequence[Mapping[str, Any]], *, keys: Sequence[str]) -> str:
    if not keys or len(set(keys)) != len(keys):
        raise ValueError("logical_keys_required")
    indexed: dict[bytes, Mapping[str, Any]] = {}
    for row in rows:
        if any(key not in row or row[key] is None for key in keys):
            raise ValueError("logical_row_key_missing")
        key = canonical_bytes([row[name] for name in keys])
        if key in indexed:
            raise ValueError("duplicate_logical_row_key")
        indexed[key] = row
    return canonical_sha256([indexed[key] for key in sorted(indexed)])


class Parents(Contract):
    source_dataset_id: SourceID | None
    curated_dataset_id: CuratedID | None
    feature_set_id: FeatureID | None
    label_dataset_id: LabelID | None
    split_id: SplitID | None
    model_id: ModelID | None
    inference_run_id: RunID | None


def no_null(value: JsonValue) -> None:
    if value is None:
        raise ValueError("resolved_parameter_cannot_be_null")
    if isinstance(value, dict):
        for item in value.values():
            no_null(item)
    elif isinstance(value, list):
        for item in value:
            no_null(item)


class SemanticConfig(Contract):
    seed: NonNegativeInt
    scenario: Symbol
    business_timezone: Literal["UTC"]
    calendar_version: Symbol
    contract_version: Literal["1.0"]
    requested_parameters: dict[Symbol, JsonValue]
    resolved_parameters: dict[Symbol, JsonValue] = Field(min_length=1)

    @model_validator(mode="after")
    def resolved(self) -> Self:
        no_null(self.resolved_parameters)
        if not set(self.requested_parameters) <= set(self.resolved_parameters):
            raise ValueError("requested_parameter_not_resolved")
        return self


class IdentityDescriptor(Contract):
    canonicalization_version: Literal["retailops-canonical-json-v1"]
    role: Role
    classification: Classification
    parents: Parents
    config: SemanticConfig
    provenance: Provenance
    logical_content_sha256: Sha256

    @model_validator(mode="after")
    def role_parents(self) -> Self:
        required = {
            "source": set(),
            "curated": {"source_dataset_id"},
            "features": {"source_dataset_id", "curated_dataset_id"},
            "labels": {"source_dataset_id", "curated_dataset_id"},
            "split": {
                "source_dataset_id",
                "curated_dataset_id",
                "feature_set_id",
                "label_dataset_id",
            },
            "predictions": {
                "source_dataset_id",
                "curated_dataset_id",
                "feature_set_id",
                "model_id",
                "inference_run_id",
            },
        }
        actual = {k for k, v in self.parents.model_dump().items() if v is not None}
        if actual != required[self.role]:
            raise ValueError("incorrect_identity_parents")
        if self.provenance.source_owner != (self.role == "source"):
            raise ValueError("incorrect_source_ownership")
        return self

    def content_id(self) -> str:
        return self.role + "-sha256-" + canonical_sha256(self.model_dump(mode="json"))
