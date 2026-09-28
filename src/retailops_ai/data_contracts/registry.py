"""Exact supported versions and safe, bounded JSON decoding."""

import json
from typing import Any

from pydantic import BaseModel

from retailops_ai.data_contracts.bundle import ContractBundle
from retailops_ai.data_contracts.dataset import DatasetManifest
from retailops_ai.data_contracts.feature import FeatureRecord
from retailops_ai.data_contracts.label import LabelRecord
from retailops_ai.data_contracts.model import ModelRecord
from retailops_ai.data_contracts.prediction import PredictionRecord
from retailops_ai.data_contracts.run import MLRunRecord
from retailops_ai.data_contracts.split import SplitRecord
from retailops_ai.data_contracts.tool import ToolRequest, ToolResult

MAX_DOCUMENT_BYTES = 2_000_000
MODELS: dict[str, type[BaseModel]] = {
    "dataset": DatasetManifest,
    "feature": FeatureRecord,
    "label": LabelRecord,
    "prediction": PredictionRecord,
    "run": MLRunRecord,
    "tool_request": ToolRequest,
    "tool_result": ToolResult,
    "split": SplitRecord,
    "model": ModelRecord,
    "bundle": ContractBundle,
}


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("nonfinite_json_number")


def validate_document(family: str, raw: bytes) -> BaseModel:
    if family not in MODELS:
        raise ValueError("unsupported_contract_family")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("contract_document_too_large")
    # Pydantic's standard JSON parser accepts duplicate names; reject ambiguity first.
    json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    return MODELS[family].model_validate_json(raw)
