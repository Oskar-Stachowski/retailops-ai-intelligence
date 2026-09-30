"""Read-only, bounded retrieval of one explicitly selected AI04 Actions artifact.

Prefer the GitHub connector's download_workflow_artifact and pass a materialized
local ZIP plus its saved metadata. If that connector reference has no local path,
the fallback uses only two fixed GitHub GET endpoints and a validated storage
redirect. Optional GH_TOKEN/GITHUB_TOKEN credentials go only to api.github.com.
Only owned temporary transport ZIPs are removed after checkpoint publication.
No workflow dispatch, remote deletion, scoring, or source generation is provided.

Run as ``python scripts/download_forecast_cohort_checkpoint.py`` from the AI repo.
REST contract: https://docs.github.com/en/rest/actions/artifacts?apiVersion=2026-03-10
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from import_forecast_cohort_checkpoint import import_checkpoint_zip

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.functional_v12_archive import verify_checkpoint
from retailops_ai.forecasting.functional_v12_resources import MAX_CHECKPOINT_BYTES
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    inventory,
    read_json,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

REPOSITORY = "Oskar-Stachowski/retailops-ai-intelligence"
API = "https://api.github.com/repos/" + REPOSITORY + "/actions/artifacts/"
API_VERSION = "2026-03-10"
MIB = 1024**2
MAX_METADATA_BYTES = MIB


class Readable(Protocol):
    def read(self, amount: int = -1) -> bytes: ...


def _storage_url(url: str) -> None:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.fragment
        or not parsed.path.startswith("/")
        or not any(
            hostname.endswith(suffix) and hostname != suffix[1:]
            for suffix in (".blob.core.windows.net", ".actions.githubusercontent.com")
        )
    ):
        raise SnapshotError("cohort_download_unapproved_storage_redirect")


@contextmanager
def open_get(
    url: str, *, token: str | None = None
) -> Iterator[tuple[int, dict[str, str], Readable]]:
    parsed = urlsplit(url)
    is_api = url.startswith(API) and parsed.hostname == "api.github.com"
    if not is_api:
        _storage_url(url)
        if token is not None:
            raise SnapshotError("cohort_download_credentials_must_not_leave_api")
    elif not re.fullmatch(re.escape(API) + r"[1-9][0-9]*(?:/zip)?", url):
        raise SnapshotError("cohort_download_endpoint_not_allowlisted")
    headers = {"User-Agent": "retailops-ai04-checkpoint-reader", "Accept-Encoding": "identity"}
    if is_api:
        headers.update(Accept="application/vnd.github+json")
        headers["X-GitHub-Api-Version"] = API_VERSION
        if token:
            headers["Authorization"] = "Bearer " + token
    connection = http.client.HTTPSConnection(
        parsed.hostname or "", timeout=30, context=ssl.create_default_context()
    )
    try:
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        try:
            yield (
                response.status,
                {key.lower(): value for key, value in response.getheaders()},
                response,
            )
        finally:
            response.close()
    finally:
        connection.close()


def artifact_metadata(artifact_id: int, token: str | None) -> dict[str, Any]:
    if type(artifact_id) is not int or artifact_id <= 0:
        raise SnapshotError("cohort_download_invalid_artifact_id")
    with open_get(API + str(artifact_id), token=token) as (status, _, stream):
        if status != 200:
            raise SnapshotError(f"cohort_download_metadata_http_{status}")
        raw = stream.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise SnapshotError("cohort_download_metadata_budget")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise SnapshotError("cohort_download_metadata_shape")
    return result


def validate_artifact(
    metadata: dict[str, Any],
    *,
    artifact_id: int,
    run_id: int,
    control_commit: str,
    freeze_id: str,
    seed: int,
    maximum_bytes: int,
    remote: bool,
) -> str:
    digest = metadata.get("digest", "")
    expected_url = API + str(artifact_id)
    if (
        len(canonical_bytes(metadata)) > MAX_METADATA_BYTES
        or type(artifact_id) is not int
        or artifact_id <= 0
        or type(run_id) is not int
        or run_id <= 0
        or type(seed) is not int
        or not 0 <= seed < 2**32
        or not re.fullmatch(r"[0-9a-f]{40}", control_commit)
        or not re.fullmatch(r"functional-v12-freeze-sha256-[0-9a-f]{64}", freeze_id)
        or type(maximum_bytes) is not int
        or not 1 <= maximum_bytes <= MAX_CHECKPOINT_BYTES
        or metadata.get("id") != artifact_id
        or metadata.get("name") != f"ai04-{freeze_id}-seed-{seed}"
        or metadata.get("url") != expected_url
        or metadata.get("archive_download_url") != expected_url + "/zip"
        or metadata.get("workflow_run", {}).get("id") != run_id
        or metadata.get("workflow_run", {}).get("head_sha") != control_commit
        or not isinstance(digest, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        or type(metadata.get("size_in_bytes")) is not int
        or not 0 < metadata["size_in_bytes"] <= maximum_bytes + MIB
        or remote
        and metadata.get("expired") is not False
    ):
        raise SnapshotError("cohort_download_artifact_identity_run_or_budget")
    return digest.removeprefix("sha256:")


def _download_zip(
    artifact_id: int, target: Path, digest: str, maximum_bytes: int, token: str | None
) -> None:
    with open_get(API + str(artifact_id) + "/zip", token=token) as (status, headers, _):
        if status != 302 or not headers.get("location"):
            raise SnapshotError(f"cohort_download_redirect_http_{status}")
        url = headers["location"]
    # GitHub documents a temporary storage redirect. Never forward its API credentials.
    for _ in range(3):
        _storage_url(url)
        with open_get(url) as (status, headers, stream):
            if status in {301, 302, 303, 307, 308}:
                if not headers.get("location"):
                    raise SnapshotError("cohort_download_missing_storage_location")
                url = headers["location"]
                continue
            if status != 200:
                raise SnapshotError(f"cohort_download_storage_http_{status}")
            if "content-length" in headers and int(headers["content-length"]) > maximum_bytes:
                raise SnapshotError("cohort_download_transfer_budget")
            checksum, count = hashlib.sha256(), 0
            with target.open("xb") as output:
                while chunk := stream.read(MIB):
                    count += len(chunk)
                    if count > maximum_bytes:
                        raise SnapshotError("cohort_download_transfer_budget")
                    checksum.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if checksum.hexdigest() != digest:
                raise SnapshotError("cohort_download_github_digest_mismatch")
            return
    raise SnapshotError("cohort_download_redirect_limit")


def _transport_result(
    imported: dict[str, Any],
    *,
    artifact_id: int,
    run_id: int,
    control_commit: str,
    connector_zip_used: bool,
    metadata: dict[str, Any],
    retained: Path,
) -> dict[str, Any]:
    return {
        **imported,
        "artifact_id": artifact_id,
        "run_id": run_id,
        "control_commit": control_commit,
        "github_digest_verified": True,
        "connector_zip_used": connector_zip_used,
        "transport_zip_retained": False,
        "transport_zip_policy": "owned_temporary_only_user_supplied_zip_untouched",
        "artifact_metadata_sha256": canonical_sha256(metadata),
        "artifact_metadata": str(retained / "github-artifact.json"),
        "transport_receipt": str(retained / "transport-receipt.json"),
        "checkpoint_manifest_sha256": file_hash(
            Path(imported["directory"]), "checkpoint_manifest.json"
        )[1],
    }


def _resume_checkpoint(
    retained: Path,
    output: Path,
    *,
    artifact_id: int,
    run_id: int,
    control_commit: str,
    freeze_id: str,
    seed: int,
    maximum: int,
    supplied_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """No ZIP and no network: reverify durable metadata plus all checkpoint bytes."""
    inventory(retained, {"github-artifact.json", "transport-receipt.json"})
    metadata = read_json(retained, "github-artifact.json")
    options: dict[str, Any] = {
        "artifact_id": artifact_id,
        "run_id": run_id,
        "control_commit": control_commit,
        "freeze_id": freeze_id,
        "seed": seed,
        "maximum_bytes": maximum,
        "remote": False,
    }
    digest = validate_artifact(metadata, **options)
    if supplied_metadata is not None and validate_artifact(supplied_metadata, **options) != digest:
        raise SnapshotError("cohort_download_saved_metadata_changed")
    receipt = read_json(retained, "transport-receipt.json")
    result = receipt["descriptor"]
    if (
        receipt["receipt_id"] != "cohort-transport-sha256-" + canonical_sha256(result)
        or not re.fullmatch(r"functional-checkpoint-sha256-[0-9a-f]{64}", result["checkpoint_id"])
        or type(result["connector_zip_used"]) is not bool
    ):
        raise SnapshotError("cohort_download_saved_receipt_identity")
    directory = output / "checkpoints" / result["checkpoint_id"]
    total = sum(
        file_hash(directory, name)[0] for name in ("payload.tar.gz", "checkpoint_manifest.json")
    )
    if total > maximum:
        raise SnapshotError("cohort_download_saved_checkpoint_budget")
    checkpoint = verify_checkpoint(directory)
    lineage = checkpoint["descriptor"]["lineage"]
    if (
        lineage["freeze_id"] != freeze_id
        or lineage["seed"] != seed
        or lineage["scope"] not in {"cohort_preparation_only", "partial_preparation_not_qualified"}
    ):
        raise SnapshotError("cohort_download_saved_checkpoint_lineage")
    imported = {
        "status": "passed",
        "scope": "transport_integrity_not_forecast_qualification",
        "checkpoint_id": checkpoint["checkpoint_id"],
        "directory": str(directory),
        "checkpoint_scope": lineage["scope"],
        "checkpoint_bytes": total,
        "zip_sha256": digest,
        "freeze_id": freeze_id,
        "seed": seed,
        "source_regenerated": False,
    }
    expected = _transport_result(
        imported,
        artifact_id=artifact_id,
        run_id=run_id,
        control_commit=control_commit,
        connector_zip_used=result["connector_zip_used"],
        metadata=metadata,
        retained=retained,
    )
    if result != expected:
        raise SnapshotError("cohort_download_saved_receipt_binding")
    return expected


def retrieve_checkpoint(
    *,
    artifact_id: int,
    run_id: int,
    control_commit: str,
    freeze: dict[str, Any],
    seed: int,
    output: Path,
    token: str | None = None,
    local_zip: Path | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = freeze["descriptor"]
    if (
        freeze["freeze_id"] != "functional-v12-freeze-sha256-" + canonical_sha256(body)
        or seed not in body["seeds"]
        or type(artifact_id) is not int
        or artifact_id <= 0
    ):
        raise SnapshotError("cohort_download_freeze_identity_or_unplanned_seed")
    maximum = min(
        body["resource_plan"]["max_checkpoint_bytes"],
        body["remote_preparation"]["max_checkpoint_bytes"],
    )
    if type(maximum) is not int or not 1 <= maximum <= MAX_CHECKPOINT_BYTES:
        raise SnapshotError("cohort_download_invalid_budget")
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=True)
    checked_directory(output)
    downloads = output / "downloads"
    downloads.mkdir(exist_ok=True)
    checked_directory(downloads)
    # One active or failed transport across the entire output, not one per seed.
    # A killed process leaves its guard and scratch for explicit investigation.
    if any(downloads.glob("failed-*")) or (downloads / ".transfer-active").exists():
        raise SnapshotError("cohort_download_unresolved_transfer_halt_before_retry")
    retained = downloads / f"artifact-{artifact_id}"
    if retained.exists():
        return _resume_checkpoint(
            retained,
            output,
            artifact_id=artifact_id,
            run_id=run_id,
            control_commit=control_commit,
            freeze_id=freeze["freeze_id"],
            seed=seed,
            maximum=maximum,
            supplied_metadata=metadata,
        )
    if (local_zip is None) != (metadata is None):
        raise SnapshotError("cohort_download_connector_zip_requires_saved_metadata")
    # mkdir is an atomic cross-process guard: concurrent calls cannot allocate two ZIPs.
    active = downloads / ".transfer-active"
    try:
        active.mkdir()
    except FileExistsError as exc:
        raise SnapshotError("cohort_download_unresolved_transfer_halt_before_retry") from exc
    try:
        if any(downloads.glob("failed-*")):
            raise SnapshotError("cohort_download_unresolved_transfer_halt_before_retry")
        supplied = metadata if metadata is not None else artifact_metadata(artifact_id, token)
        digest = validate_artifact(
            supplied,
            artifact_id=artifact_id,
            run_id=run_id,
            control_commit=control_commit,
            freeze_id=freeze["freeze_id"],
            seed=seed,
            maximum_bytes=maximum,
            remote=local_zip is None,
        )
        # One ZIP plus one extracted checkpoint. Successful ZIPs do not accumulate.
        if shutil.disk_usage(output).free < 2 * (maximum + MIB) + 64 * MIB:
            raise SnapshotError("cohort_download_insufficient_space")
        with tempfile.TemporaryDirectory(prefix=".artifact-download-", dir=active) as temporary:
            staging = Path(temporary)
            try:
                proof = staging / "proof"
                proof.mkdir()
                (proof / "github-artifact.json").write_bytes(canonical_bytes(supplied) + b"\n")
                archive = local_zip
                if archive is None:
                    archive = staging / "artifact.zip"
                    _download_zip(artifact_id, archive, digest, maximum + MIB, token)
                elif (
                    archive.is_symlink()
                    or file_hash(archive.parent, archive.name)[1] != digest
                    or archive.stat().st_size > maximum + MIB
                ):
                    raise SnapshotError("cohort_download_connector_zip_checksum_or_budget")
                imported = import_checkpoint_zip(
                    archive,
                    output / "checkpoints",
                    zip_sha256=digest,
                    freeze_id=freeze["freeze_id"],
                    seed=seed,
                    maximum_bytes=maximum,
                )
                result = _transport_result(
                    imported,
                    artifact_id=artifact_id,
                    run_id=run_id,
                    control_commit=control_commit,
                    connector_zip_used=local_zip is not None,
                    metadata=supplied,
                    retained=retained,
                )
                (proof / "transport-receipt.json").write_bytes(
                    canonical_bytes(
                        {
                            "receipt_id": "cohort-transport-sha256-" + canonical_sha256(result),
                            "descriptor": result,
                        }
                    )
                    + b"\n"
                )
                fsync_tree(proof)
                publish_noreplace(proof, retained)
                # TemporaryDirectory removes only our own redundant wrapper, after both
                # immutable checkpoint and durable transport evidence are published.
                return result
            except BaseException as exc:
                (staging / "failure.json").write_bytes(
                    canonical_bytes(
                        {
                            "error_type": type(exc).__name__,
                            "error": "artifact_download_failed",
                            "artifact_id": artifact_id,
                            "run_id": run_id,
                            "source_regenerated": False,
                            "forecast_model_status": "not_ready",
                            "further_transfers": "blocked_until_explicit_recovery",
                        }
                    )
                    + b"\n"
                )
                fsync_tree(staging)
                publish_noreplace(staging, downloads / "failed-transfer")
                raise
    finally:
        # Empty guard only; it is never allowed to delete arbitrary retained data.
        active.rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-id", type=int, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--control-commit", required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local-zip", type=Path)
    parser.add_argument("--metadata-json", type=Path)
    args = parser.parse_args()
    result = retrieve_checkpoint(
        artifact_id=args.artifact_id,
        run_id=args.run_id,
        control_commit=args.control_commit,
        freeze=json.loads(args.freeze.read_bytes()),
        seed=args.seed,
        output=args.output,
        token=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"),
        local_zip=args.local_zip,
        metadata=json.loads(args.metadata_json.read_bytes()) if args.metadata_json else None,
    )
    print(canonical_bytes(result).decode())


if __name__ == "__main__":
    main()
