"""Explicit v12 inference role and immutable, expiring local approval binding."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecast_jobs.v12_contracts import V12Execution, V12ExecutionLimits
from retailops_ai.forecast_jobs.v12_executor import inference_schema
from retailops_ai.forecast_jobs.v12_inference_contracts import V12InferenceResult
from retailops_ai.forecast_jobs.v12_runtime import LoadedV12Forecast, _execute, validate_inputs
from retailops_ai.model_lifecycle.v12_development import V12DevelopmentAcceptance
from retailops_ai.model_lifecycle.v12_release_contracts import (
    V12InferenceContext,
    V12InferenceRelease,
    V12SourcePolicy,
)
from retailops_ai.source_snapshot.files import file_hash, read_json


def source_policy(
    loaded: LoadedV12Forecast,
    inputs: PreparedInputs,
    development_acceptance: V12DevelopmentAcceptance | None = None,
) -> V12SourcePolicy:
    if file_hash(loaded.root, "signature.json") != (
        loaded.pin.signature.size_bytes,
        loaded.pin.signature.sha256,
    ):
        raise ValueError("v12_inference_signature_changed")
    schema = inference_schema(read_json(loaded.root, "signature.json")["input_schema"])
    policy = V12SourcePolicy(
        feature_set_id=inputs.feature_manifest.feature_set_id,
        curated_descriptor_sha256=inputs.feature_manifest.descriptor.parent.curated_descriptor_sha256,
        input_schema_sha256=canonical_sha256(schema),
    )
    validate_inputs(
        loaded.pin, inputs, source_policy=policy, development_acceptance=development_acceptance
    )
    return policy


def predict_acceptance(
    loaded: LoadedV12Forecast,
    inputs: PreparedInputs,
    *,
    limits: V12ExecutionLimits,
    development_acceptance: V12DevelopmentAcceptance | None = None,
) -> V12InferenceResult:
    """Ready export load/predict probe; no approval, registration or published outputs."""
    policy = source_policy(loaded, inputs, development_acceptance)
    request = V12Execution.model_validate_json(
        V12Execution(pin=loaded.pin, inputs=inputs, limits=limits).model_dump_json()
    )
    context = V12InferenceContext(
        purpose="serving_load_predict_acceptance",
        source_policy=policy,
        development_acceptance=development_acceptance,
        serving_eligible=False,
    )
    return _execute(loaded, request, inference=context)


@dataclass(frozen=True)
class LoadedV12Inference:
    export: LoadedV12Forecast
    release: V12InferenceRelease
    image_digest: str

    def predict(
        self,
        inputs: PreparedInputs,
        *,
        limits: V12ExecutionLimits | None = None,
        tick: Callable[[], None] | None = None,
    ) -> V12InferenceResult:
        """A worker may heartbeat/check fencing through tick; exceptions kill/reap the child."""
        release = V12InferenceRelease.model_validate_json(self.release.model_dump_json())
        qualification = release.qualification
        if (
            release.approval.image_digest != self.image_digest
            or self.export.pin != qualification.pin
            or not release.reviewed_at <= datetime.now(UTC) < qualification.valid_until
        ):
            raise ValueError("v12_inference_release_expired_or_pin_changed")
        selected_limits = limits or qualification.limits
        if (
            selected_limits.wall_seconds > qualification.limits.wall_seconds
            or selected_limits.rss_bytes > qualification.limits.rss_bytes
        ):
            raise ValueError("v12_inference_unreviewed_resource_limit")
        inputs = PreparedInputs.model_validate_json(inputs.model_dump_json())
        validate_inputs(
            self.export.pin,
            inputs,
            source_policy=qualification.source_policy,
            development_acceptance=qualification.development_acceptance,
        )
        if (
            source_policy(self.export, inputs, qualification.development_acceptance)
            != qualification.source_policy
        ):
            raise ValueError("v12_inference_source_policy_changed")
        request = V12Execution.model_validate_json(
            canonical_bytes(
                dict(
                    pin=self.export.pin.model_dump(mode="json"),
                    inputs=inputs.model_dump(mode="json"),
                    limits=selected_limits.model_dump(mode="json"),
                )
            )
        )

        def guard() -> None:
            if not release.reviewed_at <= datetime.now(UTC) < qualification.valid_until:
                raise ValueError("v12_inference_release_expired")
            if tick is not None:
                tick()

        guard()
        return _execute(self.export, request, inference=release.context(), tick=guard)
