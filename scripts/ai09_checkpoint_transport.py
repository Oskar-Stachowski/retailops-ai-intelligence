"""Bounded transport of trusted completed-prefix artifacts, without source generation.

GitHub run/head/artifact identity and its ZIP digest are checked independently of
the inner storage archives. Reuse the audited read-only AI04 HTTP transport; its
freeze contract and archive importer are unchanged.
"""

from __future__ import annotations

import re
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import preparation_checkpoint as checkpoints
from retailops_ai.evaluation_campaign import preparation_execution as execution
from retailops_ai.source_snapshot.files import checked_directory, file_hash, read_json, regular_file
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

VERSION = "ai09-completed-prefix-transport-1.0.0"
MAX_FILES = 256
MAX_BYTES = 4 * 1024**3
MAX_METADATA = 2 * 1024**2


def inventory(root: Path) -> dict[str, dict[str, Any]]:
    root = checked_directory(root)
    result: dict[str, dict[str, Any]] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("preparation_transport_special_file")
        name = path.relative_to(root).as_posix()
        if name == "bundle.json":
            continue
        size, digest = file_hash(root, name)
        total += size
        if total > MAX_BYTES or len(result) >= MAX_FILES:
            raise ValueError("preparation_transport_inventory_budget")
        result[name] = {"size_bytes": size, "sha256": digest}
    return result


def verify_bundle(root: Path, *, identity: dict[str, Any]) -> dict[str, Any]:
    manifest = read_json(root, "bundle.json")
    state = execution.inspect(root)
    count = state["completed_phases"]
    if (
        manifest.get("version") != VERSION
        or manifest.get("deliberate_prefix_stop") is not True
        or manifest.get("identity_sha256") != canonical_sha256(identity)
        or state["identity"] != identity
        or state["status"] != "prepared"
        or not 1 <= count < len(checkpoints.PHASES)
        or len(state["events"]) != count * 4
        or manifest.get("event_sha256") != canonical_sha256(state["events"])
        or manifest.get("completed_phases") != count
        or manifest.get("files") != inventory(root)
    ):
        raise ValueError("preparation_transport_native_history_or_inventory_mismatch")
    expected = {"preparation-plan.json", "preparation-identity.json", "execution.json"}
    expected.update(f"events/{index:03d}.json" for index in range(len(state["events"])))
    expected.update(
        "resume-receipts/" + a["receipt_sha256"] + ".json" for a in state["cost_adjustments"]
    )
    for phase in checkpoints.PHASES[:count]:
        binding = read_json(root, phase + ".checkpoint.json")
        identifier = binding.get("checkpoint_id", "")
        if not re.fullmatch(r"functional-checkpoint-sha256-[0-9a-f]{64}", identifier):
            raise ValueError("preparation_transport_checkpoint_identifier")
        path = root / "checkpoints" / phase / identifier
        checkpoints.verify_stage(path, expected=binding)
        expected.add(phase + ".checkpoint.json")
        expected.update(
            f"checkpoints/{phase}/{identifier}/{name}"
            for name in ("payload.tar.gz", "checkpoint_manifest.json")
        )
        event = state["events"][checkpoints.PHASES.index(phase) * 4 + 3]
        if event["measurement"].get("checkpoint_binding_sha256") != canonical_sha256(binding):
            raise ValueError("preparation_transport_unbound_native_checkpoint")
    if set(manifest["files"]) != expected:
        raise ValueError("preparation_transport_namespace")
    return manifest


def bundle_prefix(source: Path, output: Path) -> dict[str, Any]:
    """Seal a deliberate measured pause; never infer it from a failed run's older artifact."""
    state = execution.inspect(source)
    count = state["completed_phases"]
    if state["status"] != "prepared" or not 1 <= count < 5 or len(state["events"]) != count * 4:
        raise ValueError("preparation_transport_requires_completed_measured_prefix")
    if output.exists() or not output.is_absolute():
        raise ValueError("preparation_transport_fresh_absolute_output_required")
    checked_directory(output.parent)
    names = ["preparation-plan.json", "preparation-identity.json", "execution.json"]
    names += [f"events/{i:03d}.json" for i in range(len(state["events"]))]
    names += ["resume-receipts/" + a["receipt_sha256"] + ".json" for a in state["cost_adjustments"]]
    for phase in checkpoints.PHASES[:count]:
        binding = read_json(source, phase + ".checkpoint.json")
        identifier = binding["checkpoint_id"]
        if not re.fullmatch(r"functional-checkpoint-sha256-[0-9a-f]{64}", identifier):
            raise ValueError("preparation_transport_checkpoint_identifier")
        names.append(phase + ".checkpoint.json")
        names += [
            f"checkpoints/{phase}/{identifier}/{name}"
            for name in ("payload.tar.gz", "checkpoint_manifest.json")
        ]
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=".prefix-bundle-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for name in names:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with regular_file(source, name) as stream, target.open("xb") as destination:
                shutil.copyfileobj(stream, destination, 1024**2)
            target.chmod(0o600)
        manifest = {
            "version": VERSION,
            "identity_sha256": canonical_sha256(state["identity"]),
            "event_sha256": canonical_sha256(state["events"]),
            "completed_phases": count,
            "deliberate_prefix_stop": True,
            "files": inventory(staging),
            "prefix_copy_wall_seconds": time.perf_counter() - started,
            "project_generation_receipt": False,
            "final_test_authorized": False,
        }
        execution.write_once(staging / "bundle.json", manifest)
        verify_bundle(staging, identity=state["identity"])
        fsync_tree(staging)
        publish_noreplace(staging, output)
    return manifest


def validate_metadata(
    metadata: dict[str, Any], *, artifact_id: int, run_id: int, head: str, name: str, maximum: int
) -> str:
    from download_forecast_cohort_checkpoint import API

    digest = metadata.get("digest", "")
    if (
        type(artifact_id) is not int
        or artifact_id <= 0
        or type(run_id) is not int
        or run_id <= 0
        or not re.fullmatch(r"[0-9a-f]{40}", head)
        or type(maximum) is not int
        or not 1 <= maximum <= MAX_BYTES
        or metadata.get("id") != artifact_id
        or metadata.get("name") != name
        or metadata.get("url") != API + str(artifact_id)
        or metadata.get("archive_download_url") != API + str(artifact_id) + "/zip"
        or metadata.get("workflow_run", {}).get("id") != run_id
        or metadata.get("workflow_run", {}).get("head_sha") != head
        or metadata.get("expired") is not False
        or not isinstance(digest, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        or type(metadata.get("size_in_bytes")) is not int
        or not 0 < metadata["size_in_bytes"] <= maximum
    ):
        raise ValueError("preparation_transport_untrusted_artifact_identity")
    return digest.removeprefix("sha256:")


def import_zip(
    archive: Path, output: Path, *, expected_digest: str, identity: dict[str, Any], maximum: int
) -> dict[str, Any]:
    if (
        type(maximum) is not int
        or not 1 <= maximum <= MAX_BYTES
        or file_hash(archive.parent, archive.name)[1] != expected_digest
        or archive.stat().st_size > maximum
        or output.exists()
        or not output.is_absolute()
    ):
        raise ValueError("preparation_transport_zip_digest_or_budget")
    checked_directory(output.parent)
    with tempfile.TemporaryDirectory(prefix=".prefix-transport-", dir=output.parent) as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(archive) as zipped:
            entries = zipped.infolist()
            names = [entry.filename for entry in entries]
            if (
                len(entries) > MAX_FILES
                or len(names) != len(set(names))
                or sum(e.file_size for e in entries) > maximum
            ):
                raise ValueError("preparation_transport_zip_expansion_budget")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if (
                    path.is_absolute()
                    or not entry.filename
                    or any(
                        part in {"", ".", ".."} for part in entry.filename.rstrip("/").split("/")
                    )
                    or any(c in entry.filename for c in ("\\", "\x00", ":"))
                    or entry.flag_bits & 1
                    or entry.file_size < 0
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or entry.is_dir()
                    and entry.file_size
                    or path.as_posix() != entry.filename.rstrip("/")
                ):
                    raise ValueError("preparation_transport_zip_unsafe_member")
                target = staging / path
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if entry.filename.endswith(".json") and entry.file_size > MAX_METADATA:
                    raise ValueError("preparation_transport_metadata_budget")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                count = 0
                with zipped.open(entry) as stream, target.open("xb") as destination:
                    while chunk := stream.read(1024**2):
                        count += len(chunk)
                        if count > entry.file_size:
                            raise ValueError("preparation_transport_zip_size_changed")
                        destination.write(chunk)
                target.chmod(0o600)
                if count != entry.file_size:
                    raise ValueError("preparation_transport_zip_size_changed")
        manifest = verify_bundle(staging, identity=identity)
        fsync_tree(staging)
        publish_noreplace(staging, output)
    return manifest


def retrieve(
    *,
    artifact_id: int,
    run_id: int,
    head: str,
    name: str,
    identity: dict[str, Any],
    output: Path,
    token: str | None,
    maximum: int,
) -> dict[str, Any]:
    from download_forecast_cohort_checkpoint import _download_zip, artifact_metadata

    if output.exists() or not output.is_absolute():
        raise ValueError("preparation_transport_fresh_absolute_output_required")
    checked_directory(output.parent)
    output.mkdir(mode=0o700)
    metadata = artifact_metadata(artifact_id, token)
    digest = validate_metadata(
        metadata, artifact_id=artifact_id, run_id=run_id, head=head, name=name, maximum=maximum
    )
    execution.write_once(output / "github-artifact.json", metadata)
    archive = output / "artifact.zip"
    _download_zip(artifact_id, archive, digest, maximum, token)
    manifest = import_zip(
        archive, output / "prefix", expected_digest=digest, identity=identity, maximum=maximum
    )
    receipt = {
        "version": VERSION,
        "artifact_id": artifact_id,
        "run_id": run_id,
        "control_commit": head,
        "artifact_name": name,
        "zip_sha256": digest,
        "artifact_metadata_sha256": canonical_sha256(metadata),
        "verified_github_artifact": True,
        "deliberate_prefix_stop": manifest["deliberate_prefix_stop"],
        "event_sha256": manifest["event_sha256"],
        "identity_sha256": manifest["identity_sha256"],
        "cross_host_authorization_stripped": True,
        "source_regenerated": False,
        "project_generation_receipt": False,
    }
    execution.write_once(output / "transport.json", receipt)
    return receipt
