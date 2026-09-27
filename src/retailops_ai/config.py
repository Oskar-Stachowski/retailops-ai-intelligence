"""Explicit local configuration; loading settings never starts external services."""

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="forbid",
        case_sensitive=True,
        hide_input_in_errors=True,
        frozen=True,
    )
    app_env: Literal["local", "test"] = Field(validation_alias="APP_ENV")
    artifact_root: Path = Field(validation_alias="ARTIFACT_ROOT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", validation_alias="LOG_LEVEL"
    )

    http_host: Literal["127.0.0.1", "::1"] = Field(
        default="127.0.0.1", validation_alias="HTTP_HOST"
    )
    http_port: int = Field(default=8081, ge=1, le=65535, validation_alias="HTTP_PORT")
    metrics_token: SecretStr | None = Field(default=None, validation_alias="METRICS_TOKEN")
    readiness_timeout_seconds: float = Field(
        default=1.0, ge=0.01, le=5.0, validation_alias="READINESS_TIMEOUT_SECONDS"
    )
    build_commit: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{40}$", validation_alias="BUILD_COMMIT"
    )
    image_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$", validation_alias="IMAGE_DIGEST"
    )

    @field_validator("metrics_token")
    @classmethod
    def safe_metrics_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{32,128}", value.get_secret_value()
        ):
            raise ValueError("metrics token must contain 32-128 URL-safe characters")
        return value

    @field_validator("artifact_root", mode="before")
    @classmethod
    def nonempty_path(cls, value: object) -> object:
        if isinstance(value, str) and (not value.strip() or "\x00" in value):
            raise ValueError("artifact root must be a nonempty path")
        return value


def load_settings(env_file: Path | None = None) -> Settings:
    return Settings(_env_file=env_file, _env_file_encoding="utf-8")
