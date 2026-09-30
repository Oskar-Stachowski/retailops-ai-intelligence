"""Artifact retrieval binds one frozen cohort and never exposes API credentials."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from collections import namedtuple
from contextlib import contextmanager
from pathlib import Path

import pytest

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.functional_v12_archive import seal_checkpoint
from retailops_ai.source_snapshot.files import SnapshotError

AUTHORIZATION_SAMPLE = "non-secret-test-marker"
SCRIPTS = Path(__file__).parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "cohort_checkpoint_download", SCRIPTS / "download_forecast_cohort_checkpoint.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC.loader.exec_module(MODULE)
finally:
    sys.path.remove(str(SCRIPTS))


def fixture(
    tmp_path: Path, seed: int = 720001, artifact_id: int = 17
) -> tuple[Path, Path, dict, dict]:
    descriptor = {
        "seeds": [720001, 720002],
        "resource_plan": {"max_checkpoint_bytes": MODULE.MIB},
        "remote_preparation": {"max_checkpoint_bytes": MODULE.MIB},
    }
    freeze = {
        "freeze_id": "functional-v12-freeze-sha256-" + canonical_sha256(descriptor),
        "descriptor": descriptor,
    }
    source = tmp_path / "source"
    source.mkdir()
    (source / "facts.csv").write_text("observed_sales_units\n0\n1\n")
    checkpoint = seal_checkpoint(
        {"source": source},
        tmp_path / "sealed",
        lineage={
            "freeze_id": freeze["freeze_id"],
            "seed": seed,
            "scope": "cohort_preparation_only",
        },
    )
    archive = tmp_path / "connector.zip"
    with zipfile.ZipFile(archive, "x") as zipped:
        for path in checkpoint.iterdir():
            zipped.write(path, arcname=path.name)
    metadata = {
        "id": artifact_id,
        "name": f"ai04-{freeze['freeze_id']}-seed-{seed}",
        "url": MODULE.API + str(artifact_id),
        "archive_download_url": MODULE.API + str(artifact_id) + "/zip",
        "workflow_run": {"id": 19, "head_sha": "b" * 40},
        "digest": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
        "size_in_bytes": archive.stat().st_size,
        "expired": False,
    }
    options = {
        "artifact_id": artifact_id,
        "run_id": 19,
        "control_commit": "b" * 40,
        "freeze": freeze,
        "seed": seed,
        "output": tmp_path / "retained",
    }
    return archive, checkpoint, metadata, options


def forbid_network(*args, **kwargs):
    pytest.fail("Network must not be reached")


def test_connector_archive_is_untouched_and_offline_resume_needs_no_zip(tmp_path, monkeypatch):
    archive, original, metadata, options = fixture(tmp_path)
    monkeypatch.setattr(MODULE, "open_get", forbid_network)
    # Locally retained bytes remain useful after the remote artifact expires.
    metadata["expired"] = True
    result = MODULE.retrieve_checkpoint(local_zip=archive, metadata=metadata, **options)
    assert result["connector_zip_used"] is True
    assert result["github_digest_verified"] is True
    assert result["source_regenerated"] is False
    assert archive.exists()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["zip_sha256"]
    assert result["transport_zip_retained"] is False
    assert not list(options["output"].rglob("*.zip"))
    for path in original.iterdir():
        assert (Path(result["directory"]) / path.name).read_bytes() == path.read_bytes()
    assert MODULE.retrieve_checkpoint(local_zip=archive, metadata=metadata, **options) == result
    assert MODULE.retrieve_checkpoint(**options) == result


def test_rest_download_limits_credentials_to_fixed_api_endpoints(tmp_path, monkeypatch):
    archive, _, metadata, options = fixture(tmp_path)
    calls = []
    storage = "https://results.blob.core.windows.net/artifacts/test.zip?sig=temporary"

    @contextmanager
    def fake_get(url, *, token=None):
        calls.append((url, token))
        if url == MODULE.API + "17":
            yield 200, {}, io.BytesIO(json.dumps(metadata).encode())
        elif url == MODULE.API + "17/zip":
            yield 302, {"location": storage}, io.BytesIO()
        elif url == storage:
            yield (
                200,
                {"content-length": str(archive.stat().st_size)},
                io.BytesIO(archive.read_bytes()),
            )
        else:
            pytest.fail("Unexpected endpoint")

    monkeypatch.setattr(MODULE, "open_get", fake_get)
    result = MODULE.retrieve_checkpoint(token=AUTHORIZATION_SAMPLE, **options)
    assert result["source_regenerated"] is False
    assert result["connector_zip_used"] is False
    assert calls == [
        (MODULE.API + "17", AUTHORIZATION_SAMPLE),
        (MODULE.API + "17/zip", AUTHORIZATION_SAMPLE),
        (storage, None),
    ]
    receipt = Path(result["artifact_metadata"]).read_text()
    assert AUTHORIZATION_SAMPLE not in receipt and "temporary" not in receipt
    assert not list(options["output"].rglob("*.zip"))
    monkeypatch.setattr(MODULE, "open_get", forbid_network)
    assert MODULE.retrieve_checkpoint(**options) == result


@pytest.mark.parametrize("change", ["run", "name", "seed", "freeze", "digest"])
def test_identity_failure_blocks_before_binary_retrieval(tmp_path, monkeypatch, change):
    archive, _, metadata, options = fixture(tmp_path)
    metadata = copy.deepcopy(metadata)
    if change == "run":
        metadata["workflow_run"]["id"] += 1
    elif change == "name":
        metadata["name"] += "-other"
    elif change == "seed":
        options["seed"] = 42
    elif change == "freeze":
        options["freeze"]["descriptor"]["seeds"].append(1)
    else:
        metadata["digest"] = "missing"
    monkeypatch.setattr(MODULE, "open_get", forbid_network)
    with pytest.raises(SnapshotError, match="identity|digest|freeze"):
        MODULE.retrieve_checkpoint(local_zip=archive, metadata=metadata, **options)
    assert not (options["output"] / "checkpoints").exists()


def test_expired_remote_and_space_shortage_block_binary_retrieval(tmp_path, monkeypatch):
    archive, _, metadata, options = fixture(tmp_path)
    metadata["expired"] = True
    monkeypatch.setattr(MODULE, "artifact_metadata", lambda *_: metadata)
    monkeypatch.setattr(MODULE, "open_get", forbid_network)
    with pytest.raises(SnapshotError, match="identity_run_or_budget"):
        MODULE.retrieve_checkpoint(**options)
    metadata["expired"] = False
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(MODULE.shutil, "disk_usage", lambda _: usage(100, 100, 0))
    with pytest.raises(SnapshotError, match="insufficient_space"):
        MODULE.retrieve_checkpoint(local_zip=archive, metadata=metadata, **options)
    assert archive.exists()
    assert not (options["output"] / "checkpoints").exists()


def test_unapproved_redirect_is_rejected_before_contact(tmp_path, monkeypatch):
    calls = []

    @contextmanager
    def fake_get(url, *, token=None):
        calls.append(url)
        yield 302, {"location": "https://attacker.example/zip"}, io.BytesIO()

    monkeypatch.setattr(MODULE, "open_get", fake_get)
    with pytest.raises(SnapshotError, match="unapproved_storage_redirect"):
        MODULE._download_zip(17, tmp_path / "zip", "a" * 64, 100, AUTHORIZATION_SAMPLE)
    assert calls == [MODULE.API + "17/zip"]


@pytest.mark.parametrize("failure", ["overflow", "checksum"])
def test_transfer_limits_and_bad_digest_preserve_failed_bytes(tmp_path, monkeypatch, failure):
    archive, _, metadata, options = fixture(tmp_path)
    monkeypatch.setattr(MODULE, "artifact_metadata", lambda *_: metadata)
    storage = "https://results.blob.core.windows.net/test"
    binary = archive.read_bytes()
    if failure == "overflow":
        binary = b"0" * (2 * MODULE.MIB + 1)
    else:
        binary += b"corruption"

    @contextmanager
    def fake_get(url, *, token=None):
        if url == MODULE.API + "17/zip":
            yield 302, {"location": storage}, io.BytesIO()
        elif url == storage:
            # No Content-Length: streaming accounting must enforce the cap.
            yield 200, {}, io.BytesIO(binary)
        else:
            pytest.fail("Unexpected endpoint")

    monkeypatch.setattr(MODULE, "open_get", fake_get)
    with pytest.raises(SnapshotError, match="transfer_budget|digest_mismatch"):
        MODULE.retrieve_checkpoint(**options)
    failures = list((options["output"] / "downloads").glob("failed-*/failure.json"))
    assert len(failures) == 1
    assert json.loads(failures[0].read_bytes())["source_regenerated"] is False
    assert failures[0].with_name("artifact.zip").exists()
    assert not (options["output"] / "checkpoints").exists()
    # One failed transport is the entire permitted recovery budget, across seeds.
    monkeypatch.setattr(MODULE, "artifact_metadata", forbid_network)
    monkeypatch.setattr(MODULE, "open_get", forbid_network)
    with pytest.raises(SnapshotError, match="unresolved_transfer_halt_before_retry"):
        MODULE.retrieve_checkpoint(**options)
    with pytest.raises(SnapshotError, match="unresolved_transfer_halt_before_retry"):
        MODULE.retrieve_checkpoint(**(options | {"artifact_id": 18, "seed": 720002}))
    assert len(list((options["output"] / "downloads").glob("failed-*"))) == 1


def test_http_adapter_uses_only_get_and_rejects_credential_forwarding(monkeypatch):
    calls = []

    class Response(io.BytesIO):
        status = 200

        def getheaders(self):
            return []

    class Connection:
        def __init__(self, host, **kwargs):
            calls.append(("connect", host))

        def request(self, method, path, *, headers):
            calls.append((method, path, headers))

        def getresponse(self):
            return Response(b"{}")

        def close(self):
            pass

    monkeypatch.setattr(MODULE.http.client, "HTTPSConnection", Connection)
    with MODULE.open_get(MODULE.API + "17", token=AUTHORIZATION_SAMPLE) as (status, _, stream):
        assert status == 200 and stream.read() == b"{}"
    assert calls[1][0] == "GET"
    assert calls[1][2]["Authorization"] == "Bearer " + AUTHORIZATION_SAMPLE
    before = list(calls)
    with pytest.raises(SnapshotError, match="credentials_must_not_leave_api"):
        with MODULE.open_get(
            "https://results.blob.core.windows.net/test", token=AUTHORIZATION_SAMPLE
        ):
            pass
    assert calls == before


def test_two_cohorts_keep_only_one_temporary_zip_and_block_parallel_transfer(tmp_path, monkeypatch):
    datasets = []
    for index, seed in enumerate((720001, 720002)):
        root = tmp_path / str(seed)
        root.mkdir()
        datasets.append(fixture(root, seed, 17 + index))
    output = tmp_path / "all-cohorts"
    seen = []
    current_options = {}
    real_import = MODULE.import_checkpoint_zip

    def fake_metadata(artifact_id, _token):
        return datasets[artifact_id - 17][2]

    def fake_download(artifact_id, target, digest, maximum, _token):
        assert list(output.rglob("*.zip")) == []
        # The second caller cannot start allocating while this transfer is active.
        with pytest.raises(SnapshotError, match="unresolved_transfer_halt_before_retry"):
            MODULE.retrieve_checkpoint(**current_options)
        data = datasets[artifact_id - 17][0].read_bytes()
        assert len(data) <= maximum
        assert hashlib.sha256(data).hexdigest() == digest
        target.write_bytes(data)

    def checked_import(archive, destination, **kwargs):
        own_zips = list(output.rglob("*.zip"))
        assert own_zips == [archive]
        assert archive.stat().st_size <= kwargs["maximum_bytes"] + MODULE.MIB
        seen.append(archive)
        return real_import(archive, destination, **kwargs)

    monkeypatch.setattr(MODULE, "artifact_metadata", fake_metadata)
    monkeypatch.setattr(MODULE, "_download_zip", fake_download)
    monkeypatch.setattr(MODULE, "import_checkpoint_zip", checked_import)
    for _, _, _, options in datasets:
        current_options = options | {"output": output}
        result = MODULE.retrieve_checkpoint(**current_options)
        assert result["transport_zip_retained"] is False
        assert not list(output.rglob("*.zip"))
        assert not (output / "downloads" / ".transfer-active").exists()
    assert len(seen) == 2
    assert len(list((output / "checkpoints").iterdir())) == 2
    assert len(list((output / "downloads").iterdir())) == 2


@pytest.mark.parametrize("target", ["checkpoint", "receipt"])
def test_offline_resume_reverifies_checkpoint_and_receipt(tmp_path, monkeypatch, target):
    archive, _, metadata, options = fixture(tmp_path)
    result = MODULE.retrieve_checkpoint(local_zip=archive, metadata=metadata, **options)
    monkeypatch.setattr(MODULE, "open_get", forbid_network)
    if target == "checkpoint":
        payload = Path(result["directory"]) / "payload.tar.gz"
        payload.write_bytes(payload.read_bytes() + b"changed")
    else:
        path = Path(result["transport_receipt"])
        receipt = json.loads(path.read_bytes())
        receipt["descriptor"]["checkpoint_bytes"] += 1
        # Recalculating the receipt's own identity must not bypass actual byte checks.
        receipt["receipt_id"] = "cohort-transport-sha256-" + canonical_sha256(receipt["descriptor"])
        path.write_text(json.dumps(receipt))
    with pytest.raises(SnapshotError):
        MODULE.retrieve_checkpoint(**options)
    assert archive.exists()
    assert not list(options["output"].rglob("*.zip"))
