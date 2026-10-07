"""Recover the entire original v12 archive from bounded, temporary read-only S3 URLs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from http.client import HTTPMessage
    from typing import IO, NoReturn

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "docs/reference/ai10-v12-original-archive.json"
MAX_HANDOFF_BYTES = 4 * 1024**2
MAX_FILES = 1000
MAX_BYTES = 35 * 1024**3


class RecoveryError(ValueError):
    """Expose fixed categories, never the signed URLs or their credentials."""


def require(condition: bool, category: str) -> None:
    if not condition:
        raise RecoveryError(category)


def document(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        values = {}
        for key, value in pairs:
            require(key not in values, "v12_archive_duplicate_json_key")
            values[key] = value
        return values

    def finite(_value: str) -> None:
        raise RecoveryError("v12_archive_nonfinite_json")

    result = json.loads(raw, object_pairs_hook=unique, parse_constant=finite)
    if not isinstance(result, dict):
        raise RecoveryError("v12_archive_json_object_required")
    return result


def relative(value: str) -> str:
    path = PurePosixPath(value)
    require(
        isinstance(value, str)
        and not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {".", "..", ""} for part in path.parts)
        and all(re.fullmatch(r"[a-zA-Z0-9_.-]+", part) for part in path.parts),
        "v12_archive_relative_path_required",
    )
    return value


def validate_pin(pin: dict[str, Any]) -> None:
    files = pin["files"]
    require(
        pin["version"] == "ai10-v12-original-archive-1.0"
        and pin["bucket"] == "qnn-fs-analysis-raw-data-498283326935-eu-central-1-an"
        and pin["region"] == "eu-central-1"
        and pin["model_quality_status"] == "not_ready"
        and pin["model_refits"] == 0
        and pin["source_generation"] is False
        and 1 <= len(files) <= MAX_FILES,
        "v12_archive_frozen_pin_required",
    )
    for name, item in files.items():
        relative(name)
        require(
            name.startswith(("archive/", "support/"))
            and type(item["size_bytes"]) is int
            and 0 <= item["size_bytes"] <= MAX_BYTES
            and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is not None,
            "v12_archive_file_pin_invalid",
        )
    archived = {name: item for name, item in files.items() if name.startswith("archive/")}
    require(
        len(archived) == pin["archive_files"] == 664
        and sum(item["size_bytes"] for item in archived.values())
        == pin["archive_bytes"]
        == 31994707803
        and sum(item["size_bytes"] for item in files.values()) <= MAX_BYTES,
        "v12_archive_complete_original_inventory_required",
    )


def signed_url(url: str, pin: dict[str, Any], *, key: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    hosts = {
        pin["bucket"] + ".s3." + pin["region"] + ".amazonaws.com",
        pin["bucket"] + ".s3.amazonaws.com",
    }
    query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
    require(
        parsed.scheme == "https"
        and parsed.netloc in hosts
        and not parsed.fragment
        and urllib.parse.unquote(parsed.path) == "/" + key
        and query.get("X-Amz-Algorithm") == ["AWS4-HMAC-SHA256"]
        and len(query.get("X-Amz-Signature", [])) == 1
        and re.fullmatch(r"[0-9a-f]{64}", query["X-Amz-Signature"][0]) is not None,
        "v12_archive_scoped_read_url_required",
    )
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> NoReturn:
        raise RecoveryError("v12_archive_redirect_forbidden")


def opener() -> urllib.request.OpenerDirector:
    system_ca = Path("/etc/ssl/cert.pem")
    context = ssl.create_default_context(cafile=str(system_ca) if system_ca.is_file() else None)
    return urllib.request.build_opener(
        NoRedirect(), urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
    )


def read_handoff(url: str, digest: str) -> dict[str, Any]:
    require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "v12_handoff_digest_required")
    with opener().open(url, timeout=60) as response:
        raw = response.read(MAX_HANDOFF_BYTES + 1)
    require(
        len(raw) <= MAX_HANDOFF_BYTES and hashlib.sha256(raw).hexdigest() == digest,
        "v12_handoff_byte_binding",
    )
    return document(raw)


def bind_handoff(pin: dict[str, Any], handoff: dict[str, Any], url: str) -> dict[str, str]:
    prefix = handoff["support_prefix"]
    require(
        handoff["version"] == "ai10-v12-private-read-handoff-1.0"
        and prefix.startswith(pin["support_prefix_base"])
        and re.fullmatch(r"[0-9a-f]{32}/", prefix[len(pin["support_prefix_base"]) :]) is not None
        and set(handoff["files"]) == set(pin["files"]),
        "v12_handoff_complete_private_read_map_required",
    )
    signed_url(url, pin, key=prefix + "handoff.json")
    result = {}
    for name, value in handoff["files"].items():
        key = (
            pin["archive_prefix"] + name.removeprefix("archive/")
            if name.startswith("archive/")
            else prefix + name.removeprefix("support/")
        )
        result[name] = signed_url(value, pin, key=key)
    return result


def download(name: str, reference: dict[str, Any], url: str, output: Path) -> None:
    # The whole output directory is new and private. Existing evidence is never replaced.
    target = output / name
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = output / ".partial" / hashlib.sha256(name.encode()).hexdigest()
    digest, size = hashlib.sha256(), 0
    with staging.open("xb") as stream:
        staging.chmod(0o600)
        with opener().open(url, timeout=60) as response:
            while chunk := response.read(1024**2):
                size += len(chunk)
                require(size <= reference["size_bytes"], "v12_archive_download_size_changed")
                digest.update(chunk)
                stream.write(chunk)
        stream.flush()
        os.fsync(stream.fileno())
    require(
        size == reference["size_bytes"] and digest.hexdigest() == reference["sha256"],
        "v12_archive_download_byte_binding",
    )
    staging.rename(target)


def recover(
    pin: dict[str, Any], urls: dict[str, str], output: Path, reserve_gib: int
) -> dict[str, Any]:
    validate_pin(pin)
    require(set(urls) == set(pin["files"]), "v12_archive_incomplete_read_map")
    require(type(reserve_gib) is int and 6 <= reserve_gib <= 200, "v12_archive_reserve_invalid")
    require(
        reserve_gib >= 50
        or (
            os.getenv("GITHUB_ACTIONS") == "true"
            and os.getenv("RUNNER_OS") == "Linux"
            and os.getenv("RUNNER_ENVIRONMENT") == "github-hosted"
        ),
        "v12_archive_lower_reserve_requires_disposable_remote_runner",
    )
    total = sum(item["size_bytes"] for item in pin["files"].values())
    require(
        shutil.disk_usage(output.parent).free >= total + reserve_gib * 1024**3,
        "v12_archive_disk_reserve_would_be_breached",
    )
    output.mkdir(mode=0o700)
    try:
        partial = output / ".partial"
        partial.mkdir(mode=0o700)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(download, name, reference, urls[name], output)
                for name, reference in pin["files"].items()
            ]
            try:
                for future in futures:
                    future.result()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        partial.rmdir()
        return {
            "status": "passed",
            "scope": "complete_original_archive_bytes_only_not_serving_or_model_qualification",
            "run_id": pin["run_id"],
            "files": len(pin["files"]),
            "bytes": total,
            "source_generation": False,
            "model_refits": 0,
            "model_quality_status": "not_ready",
        }
    except BaseException:
        shutil.rmtree(output)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reserve-gib", type=int, default=50)
    args = parser.parse_args()
    try:
        pin = document(PIN.read_bytes())
        validate_pin(pin)
        url = os.environ["AI10_V12_HANDOFF_URL"]
        # Validate the outer host/path before fetching any secret URL bytes.
        prefix = pin["support_prefix_base"]
        key = urllib.parse.unquote(urllib.parse.urlsplit(url).path).removeprefix("/")
        require(
            key.startswith(prefix)
            and re.fullmatch(r"[0-9a-f]{32}/handoff[.]json", key[len(prefix) :]) is not None,
            "v12_handoff_scoped_key_required",
        )
        signed_url(url, pin, key=key)
        handoff = read_handoff(url, args.handoff_sha256)
        urls = bind_handoff(pin, handoff, url)
        report = recover(pin, urls, args.output, args.reserve_gib)
        args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        print(json.dumps(report), flush=True)
    except Exception as error:  # noqa: BLE001 - URLs/auth/HTTP bodies must never escape
        print(
            json.dumps(
                {
                    "status": "failed",
                    "category": str(error)
                    if isinstance(error, RecoveryError)
                    else "v12_archive_recovery_failed",
                }
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
