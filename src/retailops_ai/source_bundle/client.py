"""Download under a hard process deadline, then verify and atomically import native facts."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from retailops_ai.source_rest.client import ClientConfig

from .wire import MAX_BUNDLE_BYTES, BundleID, BundleManifest


class BundleClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    base_url: str
    credential: SecretStr = Field(min_length=32, max_length=256)
    allow_http_loopback: bool = False
    # Network deadline includes every file, retries and slow response bodies.
    deadline_seconds: float = Field(default=180.0, ge=0.1, le=300.0)
    bundle_id: BundleID
    max_bytes: int = Field(default=MAX_BUNDLE_BYTES, ge=1, le=MAX_BUNDLE_BYTES)
    max_files: int = Field(default=10000, ge=2, le=10000)

    @model_validator(mode="after")
    def origin(self) -> BundleClientConfig:
        ClientConfig(
            base_url=self.base_url,
            credential=self.credential,
            allow_http_loopback=self.allow_http_loopback,
        )
        return self


class BundleDownloadError(ValueError):
    pass


def download_import(
    config: BundleClientConfig,
    generated_root: Path,
    *,
    required_use_cases: tuple[str, ...] = ("forecast_source",),
) -> dict[str, Any]:
    # No generated output is touched until the entire byte inventory is downloaded.
    with tempfile.TemporaryDirectory(prefix="retailops-ai10-download-") as directory:
        private = Path(directory).resolve()
        request = {
            "base_url": config.base_url,
            "credential": config.credential.get_secret_value(),
            "bundle_id": config.bundle_id,
            "directory": str(private),
            "max_bytes": config.max_bytes,
            "max_files": config.max_files,
        }
        try:
            result = subprocess.run(  # noqa: S603 - fixed interpreter/owned worker; credentials only on stdin
                [sys.executable, "-I", str(Path(__file__).with_name("worker.py"))],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                timeout=config.deadline_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BundleDownloadError("bundle_network_deadline_exceeded") from exc
        if result.returncode != 0 or result.stdout.strip() != '{"status":"downloaded"}':
            raise BundleDownloadError("bundle_download_rejected")
        manifest = BundleManifest.model_validate_json((private / "bundle.json").read_bytes())
        native_request = {
            "download_directory": str(private),
            "generated_root": str(generated_root.absolute()),
            "required_use_cases": required_use_cases,
            "max_bytes": config.max_bytes,
            "max_files": config.max_files,
        }
        try:
            imported = subprocess.run(  # noqa: S603 - fixed private native worker; legacy model process untouched
                [sys.executable, "-I", str(Path(__file__).with_name("native_worker.py"))],
                input=json.dumps(native_request),
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise BundleDownloadError("bundle_native_import_deadline_exceeded") from exc
        if imported.returncode:
            raise BundleDownloadError("bundle_native_import_rejected")
        result_import = json.loads(imported.stdout)
        if result_import.get("status") not in {"published", "reused"}:
            raise BundleDownloadError("bundle_native_import_rejected")
        return {
            **result_import,
            "bundle_id": manifest.bundle_id,
            "source_snapshot_version": manifest.source_snapshot_version,
            "network_integrity": "passed",
            "replay_handoff": False,
        }
