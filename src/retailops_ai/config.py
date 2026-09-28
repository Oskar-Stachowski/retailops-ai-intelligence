"""Explicit local configuration; loading settings never starts external services."""

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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

    network_mode: Literal["local", "compose"] = Field(
        default="local", validation_alias="NETWORK_MODE"
    )
    database_url: SecretStr | None = Field(default=None, validation_alias="DATABASE_URL")
    http_host: Literal["127.0.0.1", "::1", "0.0.0.0"] = Field(  # noqa: S104 - controlled Compose bind
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

    @field_validator("database_url")
    @classmethod
    def valid_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        from sqlalchemy.engine import make_url
        from sqlalchemy.exc import ArgumentError

        try:
            url = make_url(value.get_secret_value())
        except ArgumentError as exc:
            raise ValueError("invalid database URL") from exc
        if (
            url.drivername != "postgresql+psycopg"
            or not url.host
            or not url.database
            or not url.username
            or not url.password
        ):
            raise ValueError("database URL requires PostgreSQL/psycopg and credentials")
        return value

    @model_validator(mode="after")
    def valid_network_boundary(self) -> "Settings":
        if self.network_mode == "compose" and self.database_url is None:
            raise ValueError("Compose mode requires a database URL")
        if self.http_host == "0.0.0.0" and self.network_mode != "compose":  # noqa: S104
            raise ValueError("all-interface binding requires Compose mode")
        return self

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
