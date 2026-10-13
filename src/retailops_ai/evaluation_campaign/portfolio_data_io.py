"""Bounded label-free inference wire with separate complete outcome/key accounting."""

import json
from collections.abc import Iterable, Iterator
from itertools import groupby
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_forecast_inference import (
    InferenceRecord,
    InferenceWindow,
)
from retailops_ai.evaluation_campaign.comparison import forecast_key_bytes
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.evaluation_campaign.portfolio_assessment import segment_values
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, read_json

MAX_ROWS = 50000
MAX_BYTES = 1024**3
MAX_LINE = 4 * 1024**2


def documents(path: Path) -> Iterator[dict[str, Any]]:
    consumed = 0
    with path.open("rb") as stream:
        while line := stream.readline(MAX_LINE + 1):
            consumed += len(line)
            if len(line) > MAX_LINE or consumed > MAX_BYTES:
                raise SnapshotError("portfolio_wire_budget")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise SnapshotError("portfolio_wire_object_required")
            yield value


def write_population(
    output: Path,
    records: Iterable[tuple[InputRow, HistoryContext, int | None, tuple[str, ...]]],
    *,
    role: str,
) -> dict[str, Any]:
    if role not in {"calibration", "final_test"}:
        raise SnapshotError("portfolio_assessment_role_required")
    keys: set[bytes] = set()
    contexts: set[str] = set()
    size = 0
    eligible = 0
    with (
        (output / "inputs.jsonl").open("xb") as inputs,
        (output / "actuals.jsonl").open("xb") as actuals,
    ):
        for identifier, group in groupby(records, key=lambda r: r[0].history_context_sha256):
            if identifier in contexts or len(contexts) >= 3000:
                raise SnapshotError("portfolio_noncontiguous_or_excess_windows")
            contexts.add(identifier)
            members: list[dict[str, Any]] = []
            history = None
            for row, current, actual, reasons in group:
                key = forecast_key_bytes(row)
                if (
                    key in keys
                    or len(keys) >= MAX_ROWS
                    or len(members) >= 14
                    or current.content_sha256() != identifier
                    or not reasons
                    and actual is None
                ):
                    raise SnapshotError("portfolio_population_binding_or_budget")
                if history is not None and current != history:
                    raise SnapshotError("portfolio_window_history_changed")
                history = current
                keys.add(key)
                eligible += int(not reasons)
                members.append(
                    {
                        "key": key.decode(),
                        "row": row.model_dump(mode="json"),
                        "eligible": not reasons,
                    }
                )
                raw = (
                    canonical_bytes(
                        {
                            "key": key.decode(),
                            "role": role,
                            "actual": actual,
                            "exclusion_reasons": reasons,
                            "horizon_days": row.horizon_days,
                            "groups": segment_values(row),
                        }
                    )
                    + b"\n"
                )
                actuals.write(raw)
                size += len(raw)
            if history is None:
                raise SnapshotError("portfolio_empty_window")
            raw = (
                canonical_bytes({"history": history.model_dump(mode="json"), "records": members})
                + b"\n"
            )
            size += len(raw)
            if len(raw) > MAX_LINE or size > MAX_BYTES:
                raise SnapshotError("portfolio_population_wire_budget")
            inputs.write(raw)
    if not keys or not eligible:
        raise SnapshotError("portfolio_empty_eligible_population")
    manifest = {
        "role": role,
        "rows": len(keys),
        "eligible": eligible,
        "windows": len(contexts),
        "files": {name: file_hash(output, name) for name in ("inputs.jsonl", "actuals.jsonl")},
        "inference_receives_outcomes": False,
    }
    (output / "population.json").write_bytes(canonical_bytes(manifest) + b"\n")
    return manifest


def inference_windows(root: Path) -> Iterator[InferenceWindow]:
    manifest = read_json(root, "population.json")
    if list(file_hash(root, "inputs.jsonl")) != manifest["files"]["inputs.jsonl"]:
        raise SnapshotError("portfolio_input_seal_changed")
    count = 0
    for document in documents(root / "inputs.jsonl"):
        if set(document) != {"history", "records"} or any(
            set(r) != {"key", "row", "eligible"} for r in document["records"]
        ):
            raise SnapshotError("portfolio_label_free_input_required")
        history = HistoryContext.model_validate_json(canonical_bytes(document["history"]))
        records = []
        for value in document["records"]:
            row = InputRow.model_validate_json(canonical_bytes(value["row"]))
            if (
                value["key"].encode() != forecast_key_bytes(row)
                or type(value["eligible"]) is not bool
            ):
                raise SnapshotError("portfolio_inference_key_binding")
            records.append(InferenceRecord(value["key"].encode(), row, value["eligible"]))
            count += 1
        yield InferenceWindow(tuple(records), history)
    if count != manifest["rows"]:
        raise SnapshotError("portfolio_inference_population_incomplete")


def scored_rows(root: Path) -> list[dict[str, Any]]:
    manifest = read_json(root, "population.json")
    if list(file_hash(root, "actuals.jsonl")) != manifest["files"]["actuals.jsonl"]:
        raise SnapshotError("portfolio_outcome_seal_changed")
    predictions: dict[str, dict[str, Any]] = {}
    for value in documents(root / "predictions.jsonl"):
        if (
            value["key"] in predictions
            or set(value["predictions"]) != set(MODELS)
            or len(predictions) >= MAX_ROWS
        ):
            raise SnapshotError("portfolio_prediction_population_changed")
        predictions[value["key"]] = value["predictions"]
    result = []
    for row in documents(root / "actuals.jsonl"):
        if row["key"] not in predictions or row["role"] != manifest["role"]:
            raise SnapshotError("portfolio_prediction_outcome_key_mismatch")
        values = predictions.pop(row["key"])
        for name, value in values.items():
            forecast = FunctionalForecast.model_validate(value)
            if (
                row["exclusion_reasons"]
                and any(getattr(forecast, f) is not None for f in ("mean", "median", "interval"))
                or not row["exclusion_reasons"]
                and (forecast.mean is None or name != "rf_mean" and forecast.median is None)
                or name == "rf_mean"
                and forecast.median is not None
            ):
                raise SnapshotError("portfolio_prediction_eligibility_or_functional")
        result.append(row | {"predictions": values})
    if predictions or len(result) != manifest["rows"]:
        raise SnapshotError("portfolio_complete_prediction_population_required")
    return result
