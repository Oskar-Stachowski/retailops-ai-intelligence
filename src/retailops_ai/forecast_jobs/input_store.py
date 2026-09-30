"""Trusted private registration, replay and verified reads of immutable inference inputs."""

import json
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import Field, TypeAdapter, model_validator
from sqlalchemy import Engine, text
from sqlalchemy.engine import RowMapping

from retailops_ai.data_contracts.common import Contract, Sha256, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.contracts import ProfileID
from retailops_ai.forecast_jobs.inputs import MAX_INPUT_BYTES, PreparedInputs
from retailops_ai.forecast_jobs.queue import checked, clock

Environment = Literal["local", "test"]
INPUT_LOCK = 505050


class RegistrationLimits(Contract):
    max_profiles: Annotated[int, Field(ge=1, le=256)] = 256
    max_storage_bytes: Annotated[int, Field(ge=1024, le=256 * 1024**2)] = 256 * 1024**2


class RegisteredInputs(Contract):
    environment: Environment
    registered_at: UtcTime
    profile_sha256: Sha256
    storage_bytes: Annotated[int, Field(ge=1, le=64 * 1024**2)]
    inputs: PreparedInputs

    @model_validator(mode="after")
    def integrity(self) -> Self:
        raw = self.inputs.model_dump(mode="json")
        if (
            self.profile_sha256 != canonical_sha256(raw)
            or len(canonical_bytes(raw)) > MAX_INPUT_BYTES
            or self.inputs.as_of_time > self.registered_at
            or (
                self.inputs.source_freshness is not None
                and self.inputs.source_freshness.watermark is not None
                and self.inputs.source_freshness.watermark.as_of_time > self.registered_at
            )
        ):
            raise ValueError("registered_inputs_integrity_mismatch")
        return self


def registration(row: RowMapping | Mapping[str, Any]) -> RegisteredInputs:
    # SQLAlchemy mappings are converted before strict JSON validation.
    value = dict(row)
    return RegisteredInputs.model_validate_json(
        json.dumps(
            {
                "environment": value["environment"],
                "registered_at": value["registered_at"].isoformat(),
                "profile_sha256": value["profile_sha256"],
                "storage_bytes": value["storage_bytes"],
                "inputs": value["profile"],
            }
        )
    )


class PostgresInputStore:
    def __init__(
        self, engine: Engine, environment: Environment, limits: RegistrationLimits | None = None
    ) -> None:
        self.engine = engine
        self.environment: Environment = TypeAdapter(Environment).validate_python(environment)
        self.limits = RegistrationLimits.model_validate_json(
            (limits or RegistrationLimits()).model_dump_json()
        )

    def register(self, inputs: PreparedInputs) -> RegisteredInputs:
        inputs = PreparedInputs.model_validate_json(inputs.model_dump_json())
        raw = canonical_bytes(inputs.model_dump(mode="json"))
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("prepared_inputs_byte_limit")
        with self.engine.begin() as connection:
            checked(connection)
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": INPUT_LOCK})
            now = clock(connection)
            if inputs.as_of_time > now:
                raise ValueError("prepared_inputs_from_future")
            if (
                inputs.source_freshness
                and inputs.source_freshness.watermark
                and inputs.source_freshness.watermark.as_of_time > now
            ):
                raise ValueError("prepared_inputs_watermark_from_future")
            old = (
                connection.execute(
                    text(
                        "SELECT * FROM ai.forecast_prepared_inputs WHERE environment=:env AND profile_id=:id"
                    ),
                    {"env": self.environment, "id": inputs.profile_id},
                )
                .mappings()
                .first()
            )
            if old is not None:
                result = registration(old)
                if result.inputs != inputs:
                    raise ValueError("prepared_inputs_registration_conflict")
                return result
            counts = (
                connection.execute(
                    text(
                        "SELECT count(*) AS count,coalesce(sum(storage_bytes),0) AS bytes FROM ai.forecast_prepared_inputs WHERE environment=:env"
                    ),
                    {"env": self.environment},
                )
                .mappings()
                .one()
            )
            if (
                counts["count"] >= self.limits.max_profiles
                or counts["bytes"] + len(raw) > self.limits.max_storage_bytes
            ):
                raise ValueError("prepared_inputs_capacity_exceeded")
            row = (
                connection.execute(
                    text("""INSERT INTO ai.forecast_prepared_inputs(environment,profile_id,profile,profile_sha256)
                VALUES (:env,:id,CAST(:profile AS jsonb),:sha) RETURNING *"""),
                    {
                        "env": self.environment,
                        "id": inputs.profile_id,
                        "profile": raw.decode(),
                        "sha": canonical_sha256(inputs.model_dump(mode="json")),
                    },
                )
                .mappings()
                .one()
            )
            result = registration(row)
            if counts["bytes"] + result.storage_bytes > self.limits.max_storage_bytes:
                raise ValueError("prepared_inputs_capacity_exceeded")
            return result

    def get(self, profile_id: str) -> RegisteredInputs:
        TypeAdapter(ProfileID).validate_python(profile_id)
        with self.engine.begin() as connection:
            checked(connection)
            row = (
                connection.execute(
                    text(
                        "SELECT * FROM ai.forecast_prepared_inputs WHERE environment=:env AND profile_id=:id"
                    ),
                    {"env": self.environment, "id": profile_id},
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ValueError("prepared_inputs_not_registered")
            return registration(row)
