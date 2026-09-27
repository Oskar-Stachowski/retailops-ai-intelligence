"""Versioned CLI metadata; not a prediction or health response."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApplicationInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: Literal["1.0"] = "1.0"
    service: Literal["retailops-ai-intelligence"] = "retailops-ai-intelligence"
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[a-z0-9.+-]+)?$")
    implementation_status: Literal["in_progress"] = "in_progress"
