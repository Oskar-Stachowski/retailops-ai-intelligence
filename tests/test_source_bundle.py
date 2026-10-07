"""Actual socket transport, native verification and bounded failure paths."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr

from retailops_ai.source_bundle.client import (
    BundleClientConfig,
    BundleDownloadError,
    download_import,
)
from retailops_ai.source_bundle.wire import BundleManifest, canonical

FIXTURE = Path(__file__).parents[1] / "data/fixtures/ai-smoke-v1/snapshot"
TOKEN = "bundle-fixture-" + uuid4().hex


def envelope() -> dict[str, Any]:
    source = json.loads((FIXTURE / "snapshot_manifest.json").read_bytes())
    files = []
    for path in sorted(p for p in FIXTURE.rglob("*") if p.is_file()):
        relative = path.relative_to(FIXTURE).as_posix()
        raw = path.read_bytes()
        files.append(
            {
                "file_id": hashlib.sha256(relative.encode()).hexdigest(),
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
        )
    desc = {
        "version": "retailops-source-bundle-1.0",
        "snapshot_id": source["snapshot_id"],
        "source_dataset_id": source["source_dataset_id"],
        "source_snapshot_version": source["schema_version"],
        "snapshot_manifest_sha256": hashlib.sha256(
            (FIXTURE / "snapshot_manifest.json").read_bytes()
        ).hexdigest(),
        "files": files,
        "total_bytes": sum(f["bytes"] for f in files),
        "replay_handoff": False,
    }
    return {
        **desc,
        "bundle_id": "source-bundle-sha256-" + hashlib.sha256(canonical(desc)).hexdigest(),
    }


@contextmanager
def server(document: dict[str, Any], mode: str = "normal") -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            assert self.headers.get("Authorization") == "Bearer " + TOKEN
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "/credential-leak")
                self.end_headers()
                return
            if self.path.endswith("/manifest"):
                raw = json.dumps(document).encode()
            else:
                file_id = self.path.rsplit("/", 1)[-1]
                ref = next(f for f in document["files"] if f["file_id"] == file_id)
                raw = (FIXTURE / ref["path"]).read_bytes()
                if mode == "corrupt":
                    raw = b"corrupt"
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                if mode == "slow":
                    for byte in raw[:100]:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.1)
                else:
                    self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, _format: str, *args: Any) -> None:
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(http.server_port), requests
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


def config(url: str, document: dict[str, Any], **kwargs: Any) -> BundleClientConfig:
    return BundleClientConfig(
        base_url=url,
        credential=SecretStr(TOKEN),
        allow_http_loopback=True,
        bundle_id=document["bundle_id"],
        **kwargs,
    )


def test_real_download_verifies_full_fixture_and_reuses_immutable_import(tmp_path: Path) -> None:
    import retailops_ai.source_snapshot as legacy
    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.forecasting.functional_v12_campaign import campaign_code

    frozen = campaign_code()
    legacy_paths = list(legacy.__path__)
    document = envelope()
    BundleManifest.model_validate_json(json.dumps(document))
    with server(document) as (url, requests):
        first = download_import(config(url, document), tmp_path / "data/generated")
        second = download_import(config(url, document), tmp_path / "data/generated")
    assert first["status"] == "published" and second["status"] == "reused"
    assert first["snapshot_id"] == second["snapshot_id"] == document["snapshot_id"]
    assert first["source_dataset_id"] == document["source_dataset_id"]
    assert first["tables"] == 25 and first["rows"] == 31171
    assert first["replay_handoff"] is False
    assert len(requests) == 2 * (len(document["files"]) + 1)
    assert list(legacy.__path__) == legacy_paths
    assert campaign_code() == frozen
    pin = json.loads(
        (
            Path(__file__).resolve().parents[1] / "src/retailops_ai/source_bundle/upstream.json"
        ).read_bytes()
    )
    assert canonical_sha256(frozen) == pin.get(
        "shared_campaign_implementation_pin_sha256", pin["accepted_main_campaign_pin_sha256"]
    )
    assert pin["frozen_v12_campaign_pin_sha256"] == (
        "8f12dc3744880f1b2a68b4a009640dce4dcf543d8b3396c038bc175c7e3ee011"
    )


@pytest.mark.parametrize("mode", ["corrupt", "redirect"])
def test_failed_transport_never_creates_generated_output(tmp_path: Path, mode: str) -> None:
    document = envelope()
    with server(document, mode) as (url, requests):
        with pytest.raises(BundleDownloadError, match="bundle_download_rejected") as error:
            download_import(config(url, document), tmp_path / "data/generated")
    assert TOKEN not in str(error.value)
    assert not (tmp_path / "data/generated").exists()
    assert "/credential-leak" not in requests


def test_hard_deadline_bounds_continuously_slow_response(tmp_path: Path) -> None:
    document = envelope()
    with server(document, "slow") as (url, _):
        started = time.monotonic()
        with pytest.raises(BundleDownloadError, match="bundle_network_deadline_exceeded"):
            download_import(
                config(url, document, deadline_seconds=0.7), tmp_path / "data/generated"
            )
        assert time.monotonic() - started < 2
    assert not (tmp_path / "data/generated").exists()


@pytest.mark.parametrize(
    "mutation", ["traversal", "truth_path", "replay_claim", "duplicate_file", "version"]
)
def test_resealed_unsafe_envelope_is_rejected_before_file_download(
    tmp_path: Path, mutation: str
) -> None:
    document = envelope()
    if mutation in {"traversal", "truth_path"}:
        ref = document["files"][0]
        ref["path"] = (
            "facts/../../escape" if mutation == "traversal" else "evaluation_truth/labels.parquet"
        )
        ref["file_id"] = hashlib.sha256(ref["path"].encode()).hexdigest()
    elif mutation == "replay_claim":
        document["replay_handoff"] = True
    elif mutation == "duplicate_file":
        document["files"].append(document["files"][0])
        document["total_bytes"] += document["files"][0]["bytes"]
    else:
        document["source_snapshot_version"] = "9.0.0"
    desc = {k: v for k, v in document.items() if k != "bundle_id"}
    document["bundle_id"] = "source-bundle-sha256-" + hashlib.sha256(canonical(desc)).hexdigest()
    with server(document) as (url, requests):
        with pytest.raises(BundleDownloadError):
            download_import(config(url, document), tmp_path / "data/generated")
    assert len(requests) == 1
    assert not (tmp_path / "data/generated").exists()


def test_declared_download_budget_is_enforced_before_artifacts(tmp_path: Path) -> None:
    document = envelope()
    with server(document) as (url, requests):
        with pytest.raises(BundleDownloadError):
            download_import(config(url, document, max_bytes=1), tmp_path / "data/generated")
    assert len(requests) == 1 and not (tmp_path / "data/generated").exists()


def test_unsupported_source_use_case_cannot_publish_an_import(tmp_path: Path) -> None:
    document = envelope()
    with server(document) as (url, _):
        with pytest.raises(ValueError):
            download_import(
                config(url, document), tmp_path / "data/generated", required_use_cases=("replay",)
            )
    assert not (tmp_path / "data/generated").exists()
