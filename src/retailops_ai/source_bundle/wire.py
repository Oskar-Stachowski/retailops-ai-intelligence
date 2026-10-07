"""Closed transport envelope; original source manifests remain unchanged."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BundleID = Annotated[str, Field(pattern=r"^source-bundle-sha256-[0-9a-f]{64}$")]
MAX_FILE_BYTES = 64 * 1024**2
MAX_BUNDLE_BYTES = 2 * 1024**3


def canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class BundleFile(Wire):
    file_id: Digest
    path: str = Field(min_length=1, max_length=240)
    sha256: Digest
    bytes: int = Field(ge=0, le=MAX_FILE_BYTES)

    @model_validator(mode="after")
    def safe_reference(self) -> BundleFile:
        parts = self.path.split("/")
        valid = self.path in {"snapshot_manifest.json", "manifest.sha256"} or (
            len(parts) >= 2 and parts[0] in {"facts", "schemas", "reports", "manifests"}
        )
        if (
            not valid
            or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) for part in parts)
            or self.file_id != hashlib.sha256(self.path.encode()).hexdigest()
        ):
            msg = "unsafe_bundle_reference"
            raise ValueError(msg)
        return self


class BundleManifest(Wire):
    version: Literal["retailops-source-bundle-1.0"]
    bundle_id: BundleID
    snapshot_id: Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")]
    source_dataset_id: Annotated[str, Field(pattern=r"^source-sha256-[0-9a-f]{64}$")]
    source_snapshot_version: Literal["1.0.0", "1.1.0", "1.2.0"]
    snapshot_manifest_sha256: Digest
    files: tuple[BundleFile, ...] = Field(min_length=2, max_length=10000)
    total_bytes: int = Field(ge=1, le=MAX_BUNDLE_BYTES)
    replay_handoff: Literal[False] = False

    @model_validator(mode="after")
    def identity(self) -> BundleManifest:
        paths = [file.path for file in self.files]
        if (
            paths != sorted(set(paths))
            or not {"snapshot_manifest.json", "manifest.sha256"} <= set(paths)
            or sum(file.bytes for file in self.files) != self.total_bytes
            or next(file.sha256 for file in self.files if file.path == "snapshot_manifest.json")
            != self.snapshot_manifest_sha256
            or self.bundle_id
            != "source-bundle-sha256-"
            + hashlib.sha256(
                canonical(self.model_dump(mode="json", exclude={"bundle_id"}))
            ).hexdigest()
        ):
            msg = "bundle_identity_mismatch"
            raise ValueError(msg)
        return self
