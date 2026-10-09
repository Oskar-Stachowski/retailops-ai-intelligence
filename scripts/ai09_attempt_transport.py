"""Read-only final-attempt retrieval; use inside the resource-guarded resume worker.

Only two fixed GitHub run/job metadata routes are added here. The existing AI04
artifact transport still owns ZIP download and credential-free storage redirects.
No dispatch or retry is provided. The workflow must upload attempt.json only after
all preparation work has terminated, alongside its existing logs/evidence upload.
"""

from __future__ import annotations

import http.client
import json
import ssl
import stat
import zipfile
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import preparation_attempt as attempt
from retailops_ai.evaluation_campaign import preparation_execution as execution
from retailops_ai.source_snapshot.files import checked_directory

MAX_BYTES = 2 * 1024**2


def github_metadata(kind: str, identifier: int, token: str | None) -> dict[str, Any]:
    if kind not in {"runs", "jobs"} or type(identifier) is not int or identifier <= 0:
        raise ValueError("preparation_attempt_metadata_route")
    from download_forecast_cohort_checkpoint import API_VERSION

    connection = http.client.HTTPSConnection(
        "api.github.com", timeout=30, context=ssl.create_default_context()
    )
    headers = {
        "User-Agent": "retailops-ai09-attempt-reader",
        "Accept": "application/vnd.github+json",
        "Accept-Encoding": "identity",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        connection.request(
            "GET", f"/repos/{attempt.REPOSITORY}/actions/{kind}/{identifier}", headers=headers
        )
        response = connection.getresponse()
        try:
            # Never follow an API redirect with credentials or expose a response body.
            if response.status != 200:
                raise ValueError(f"preparation_attempt_metadata_http_{response.status}")
            raw = response.read(MAX_BYTES + 1)
        finally:
            response.close()
    finally:
        connection.close()
    if len(raw) > MAX_BYTES:
        raise ValueError("preparation_attempt_metadata_budget")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("preparation_attempt_metadata_shape")
    return parsed


def read_snapshot_zip(archive: Path) -> dict[str, Any]:
    """A dedicated final-attempt artifact has exactly one bounded, regular JSON file."""
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        if len(entries) != 1:
            raise ValueError("preparation_attempt_zip_namespace")
        entry = entries[0]
        if (
            entry.filename != "attempt.json"
            or entry.is_dir()
            or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG}
            or entry.flag_bits & 1
            or not 0 < entry.file_size <= MAX_BYTES
        ):
            raise ValueError("preparation_attempt_zip_namespace")
        with zipped.open(entry) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) != entry.file_size:
            raise ValueError("preparation_attempt_zip_size")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("preparation_attempt_snapshot_shape")
    return parsed


def retrieve_history(
    previous: Path,
    output: Path,
    *,
    artifact_id: int,
    run_id: int,
    job_id: int,
    identity: dict[str, Any],
    token: str | None,
) -> dict[str, Any]:
    from ai09_checkpoint_transport import validate_metadata
    from download_forecast_cohort_checkpoint import _download_zip, artifact_metadata

    state = execution.inspect(previous)
    if (
        state["identity"] != identity
        or state["status"] != "prepared"
        or not output.is_absolute()
        or ".." in output.parts
        or output.exists()
    ):
        raise ValueError("preparation_attempt_fresh_owned_output_and_exact_identity_required")
    checked_directory(output.parent)
    head = identity["consumer_commit"]
    run, job = github_metadata("runs", run_id, token), github_metadata("jobs", job_id, token)
    if run.get("id") != run_id or job.get("id") != job_id:
        raise ValueError("preparation_attempt_metadata_id_mismatch")
    number = run.get("run_attempt")
    metadata = artifact_metadata(artifact_id, token)
    digest = validate_metadata(
        metadata,
        artifact_id=artifact_id,
        run_id=run_id,
        head=head,
        name=f"ai09-preparation-attempt-{head}-{run_id}-{number}",
        maximum=MAX_BYTES,
    )
    remote = {
        "authenticated_github_download": True,
        "downloaded_zip_sha256": digest,
        "run": run,
        "job": job,
        "artifact": metadata,
        "snapshot_sha256": "0" * 64,
    }
    # Reject live/old/foreign attempts before downloading their contents.
    attempt.terminal_job_bound(remote, head=head, snapshot_sha256="0" * 64)
    output.mkdir(mode=0o700)
    execution.write_once(output / "github-run.json", run)
    execution.write_once(output / "github-job.json", job)
    execution.write_once(output / "github-artifact.json", metadata)
    archive = output / "artifact.zip"
    _download_zip(artifact_id, archive, digest, MAX_BYTES, token)
    archive.chmod(0o600)
    if archive.stat().st_size != metadata["size_in_bytes"]:
        raise ValueError("preparation_attempt_zip_metadata_size_mismatch")
    captured = read_snapshot_zip(archive)
    current = github_metadata("runs", run_id, token)
    if any(
        current.get(k) != run.get(k)
        for k in ("id", "head_sha", "run_attempt", "status", "conclusion")
    ):
        raise ValueError("preparation_attempt_remote_changed_during_download")
    remote["snapshot_sha256"] = canonical_sha256(captured)
    history = attempt.bind_history(previous, captured=captured, authenticated_remote=remote)
    execution.write_once(output / "attempt.json", captured)
    execution.write_once(output / "verified-history.json", history)
    return history
