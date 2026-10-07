"""Bounded disposable download process; no redirects, proxies or credential logs."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def main() -> int:
    try:
        config = json.loads(sys.stdin.buffer.read(65537))
        origin = urlsplit(config["base_url"])
        if (
            origin.scheme not in {"https", "http"}
            or (
                origin.scheme == "http" and origin.hostname not in {"127.0.0.1", "localhost", "::1"}
            )
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path not in {"", "/"}
            or not re.fullmatch(r"source-bundle-sha256-[0-9a-f]{64}", config["bundle_id"])
        ):
            raise ValueError("configured_origin_required")
        spec = importlib.util.spec_from_file_location(
            "bundle_wire", Path(__file__).with_name("wire.py")
        )
        if spec is None or spec.loader is None:
            raise ValueError("wire_unavailable")
        wire = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = wire
        spec.loader.exec_module(wire)
        context = ssl.create_default_context()
        for path in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if Path(path).is_file():
                context = ssl.create_default_context(cafile=path)
                break
        opener = urllib.request.build_opener(
            NoRedirect(),
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context),
        )
        base = config["base_url"].rstrip("/") + "/integration/bundles/v1/" + config["bundle_id"]

        def fetch(url: str, maximum: int) -> bytes:
            request = urllib.request.Request(  # noqa: S310 - validated HTTP(S) origin; redirects/proxies disabled
                url, headers={"Authorization": "Bearer " + config["credential"]}, method="GET"
            )
            for attempt in range(2):
                try:
                    with opener.open(request, timeout=5) as response:
                        if response.headers.get("Content-Encoding", "identity") != "identity":
                            raise ValueError("encoded_bundle_response")
                        raw: bytes = response.read(maximum + 1)
                        if len(raw) > maximum:
                            raise ValueError("bundle_response_limit")
                        return raw
                except urllib.error.HTTPError as exc:
                    if attempt == 1 or exc.code not in {429, 502, 503, 504}:
                        raise
                except (urllib.error.URLError, TimeoutError):
                    if attempt == 1:
                        raise
            raise ValueError("download_unavailable")

        raw = fetch(base + "/manifest", 4 * 1024**2)
        manifest = wire.BundleManifest.model_validate_json(
            json.dumps(json.loads(raw, object_pairs_hook=unique_pairs))
        )
        if (
            manifest.bundle_id != config["bundle_id"]
            or manifest.total_bytes > config["max_bytes"]
            or len(manifest.files) > config["max_files"]
        ):
            raise ValueError("bundle_request_mismatch")
        root = Path(config["directory"])
        snapshot = root / "snapshot"
        snapshot.mkdir(mode=0o700)
        for reference in manifest.files:
            data = fetch(base + "/files/" + reference.file_id, reference.bytes)
            if len(data) != reference.bytes or hashlib.sha256(data).hexdigest() != reference.sha256:
                raise ValueError("bundle_checksum_mismatch")
            path = snapshot / reference.path
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
        (root / "bundle.json").write_bytes(raw)
        sys.stdout.write('{"status":"downloaded"}\n')
        return 0
    except urllib.error.HTTPError as exc:
        sys.stdout.write(json.dumps({"error": "bundle_http_denied", "status": exc.code}) + "\n")
    except Exception:  # noqa: BLE001 - fixed errors only; never expose secret-bearing exceptions
        sys.stdout.write('{"error":"bundle_download_rejected"}\n')
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
