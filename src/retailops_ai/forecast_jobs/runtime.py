"""Load an approved numeric release once; reuse AI04 inference without alias reload or refitting."""

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Protocol

from pydantic import Field

from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifest_contract import FeaturePolicy
from retailops_ai.forecasting.model_contract import ModelPipeline
from retailops_ai.forecasting.model_trees import ForecastAdapter
from retailops_ai.forecasting.models import decode_model_json
from retailops_ai.model_lifecycle.baseline import BaselineInput, BaselinePipeline, predict_examples
from retailops_ai.model_lifecycle.contracts import MODEL, Binding, Receipt, Release
from retailops_ai.model_lifecycle.mlflow import MAX_METADATA, MAX_MODEL
from retailops_ai.security.local import strict_json


class RuntimePin(Contract):
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    dependency_lock_sha256: Sha256


class TrustedRegistry(Protocol):
    def validate(self, binding: Binding) -> None: ...
    def artifact(self, source_uri: str, name: str, *, limit: int = MAX_METADATA) -> bytes: ...


def exact(registry: TrustedRegistry, binding: Binding, name: str, receipt: Receipt) -> bytes:
    value = registry.artifact(
        binding.source_uri, name, limit=MAX_MODEL if name == "model.json" else MAX_METADATA
    )
    if (len(value), hashlib.sha256(value).hexdigest()) != (receipt.size_bytes, receipt.sha256):
        raise ValueError("runtime_loaded_artifact_checksum_mismatch")
    strict_json(value)
    return value


@dataclass(frozen=True)
class LoadedForecast:
    release: Release
    runtime_pin: RuntimePin
    feature_policy: FeaturePolicy
    selection_cutoff: datetime
    model_id: str
    _adapter: ForecastAdapter | BaselinePipeline = field(repr=False)

    def predict(self, inputs: PreparedInputs) -> tuple[float, ...]:
        inputs = PreparedInputs.model_validate_json(inputs.model_dump_json())
        if (
            inputs.feature_manifest.descriptor.resolved_policy != self.feature_policy
            or inputs.feature_manifest.descriptor.code.dependency_lock_sha256
            != self.runtime_pin.dependency_lock_sha256
        ):
            raise ValueError("runtime_inference_feature_compatibility_mismatch")
        if inputs.as_of_time <= self.selection_cutoff or inputs.as_of_time > datetime.now(UTC):
            raise ValueError("runtime_model_selection_or_origin_not_known")
        histories = {h.content_sha256(): h for h in inputs.histories}
        policy = self.feature_policy
        for row in inputs.rows:
            history = histories[row.history_context_sha256]
            known = [p.business_date for p in history.points if p.status != "missing"]
            if (
                row.history_active_days < policy.minimum_active_history_days
                or row.history_known_days < policy.minimum_known_history_days
                or not known
                or (row.forecast_origin.date() - max(known)).days
                > policy.maximum_observation_age_days
                or not row.target_calendar_eligible
            ):
                raise ValueError("runtime_input_insufficient_stale_or_calendar_closed")
        quantities: list[float] = []
        for offset in range(0, len(inputs.rows), 256):
            rows = list(inputs.rows[offset : offset + 256])
            if isinstance(self._adapter, ForecastAdapter):
                quantities.extend(self._adapter.infer(rows, policy=policy))
            else:
                examples = [
                    BaselineInput(row=r, history=histories[r.history_context_sha256]) for r in rows
                ]
                quantities.extend(predict_examples(self._adapter, examples))
        if len(quantities) != len(inputs.rows) or any(
            not math.isfinite(q) or q < 0 for q in quantities
        ):
            raise ValueError("runtime_output_count_or_domain_mismatch")
        return tuple(quantities)


def load_release(
    release: Release, runtime_pin: RuntimePin, registry: TrustedRegistry
) -> LoadedForecast:
    release = Release.model_validate_json(release.model_dump_json())
    runtime_pin = RuntimePin.model_validate_json(runtime_pin.model_dump_json())
    binding, qualification = release.binding, release.binding.qualification
    if (
        binding.model_name != MODEL
        or qualification.purpose != "qualified_forecast"
        or any(g.status != "passed" for g in qualification.gates.values())
    ):
        raise ValueError("runtime_requires_qualified_forecast_release")
    if (
        release.image_digest != runtime_pin.image_digest
        or qualification.dependency_lock_sha256 != runtime_pin.dependency_lock_sha256
    ):
        raise ValueError("runtime_image_or_dependency_lock_mismatch")
    registry.validate(
        binding
    )  # Validates real immutable version, quality evidence, signature, freshness and smoke.
    model = exact(registry, binding, "model.json", qualification.model)
    signature = json.loads(exact(registry, binding, "signature.json", qualification.signature))
    config = json.loads(exact(registry, binding, "config.json", qualification.config))
    if not isinstance(config, dict) or "feature_policy" not in config:
        raise ValueError("runtime_config_requires_feature_policy")
    policy = FeaturePolicy.model_validate_json(json.dumps(config["feature_policy"]))
    schema = (
        BaselineInput.model_json_schema()
        if qualification.flavor == "baseline-json-v1"
        else InputRow.model_json_schema()
    )
    if signature != {
        "input_schema_sha256": canonical_sha256(schema),
        "feature_set_id": qualification.feature_set_id,
        "target_type": "observed_sales_units",
        "output": "nonnegative_units",
    }:
        raise ValueError("runtime_input_signature_mismatch")
    if qualification.flavor == "baseline-json-v1":
        baseline = BaselinePipeline.model_validate_json(model)
        if baseline.feature_set_id != qualification.feature_set_id:
            raise ValueError("runtime_baseline_training_lineage_mismatch")
        return LoadedForecast(
            release, runtime_pin, policy, baseline.selection_cutoff, baseline.model_id, baseline
        )
    pipeline = ModelPipeline.model_validate_json(json.dumps(decode_model_json(model)))
    desc = pipeline.descriptor
    if (
        desc.feature_set_id != qualification.feature_set_id
        or desc.split_id != qualification.split_id
        or desc.family != qualification.model_family
        or desc.code.dependency_lock_sha256 != qualification.dependency_lock_sha256
        or desc.preprocessing.policy != policy
    ):
        raise ValueError("runtime_pipeline_training_binding_mismatch")
    return LoadedForecast(
        release,
        runtime_pin,
        policy,
        desc.preprocessing.fold.selection_cutoff,
        pipeline.model_id,
        ForecastAdapter(pipeline),
    )
