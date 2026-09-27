"""Explicit local configuration; loading settings never starts external services."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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

    @field_validator("artifact_root", mode="before")
    @classmethod
    def nonempty_path(cls, value: object) -> object:
        if isinstance(value, str) and (not value.strip() or "\x00" in value):
            raise ValueError("artifact root must be a nonempty path")
        return value


def load_settings(env_file: Path | None = None) -> Settings:
    return Settings(_env_file=env_file, _env_file_encoding="utf-8")
