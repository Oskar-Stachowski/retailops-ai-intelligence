"""The native acceptance exporter cannot publish its private operator files or overwrite evidence."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_anomaly_oci import export_native_output  # noqa: E402


@pytest.fixture
def public_acceptance(tmp_path):
    source = tmp_path / "private-acceptance"
    census = "native-model-outbox-sha256-" + "a" * 64
    directory = source / "native-outbox" / census
    directory.mkdir(parents=True)
    for name in ("receipt.json", "events.jsonl"):
        (directory / name).write_text("{}")
    (source / "native-batch-output.json").write_text("{}")
    (source / "native-frozen-model.json").write_text("{}")
    (source / "operator-credentials.json").write_text("private synthetic sentinel")
    report = {"status": "passed", "acceptance": {"native_outbox_census_id": census}}
    return source, report, tmp_path / "public"


def test_public_export_copies_only_output_and_census(public_acceptance):
    source, report, output = public_acceptance
    export_native_output(source, report, output)
    assert len(list(output.rglob("*.*"))) == 5
    assert not (output / "operator-credentials.json").exists()
    assert json.loads((output / "acceptance.json").read_text()) == report
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.rglob("*.*"))


def test_public_export_never_replaces_existing_evidence(public_acceptance):
    source, report, output = public_acceptance
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        export_native_output(source, report, output)
    assert sentinel.read_text() == "preserve"


def test_missing_original_census_does_not_publish_partial_output(public_acceptance):
    source, report, output = public_acceptance
    (source / "native-batch-output.json").unlink()
    with pytest.raises(FileNotFoundError):
        export_native_output(source, report, output)
    assert not output.exists()


def test_failed_acceptance_never_exports_output(public_acceptance):
    source, report, output = public_acceptance
    report["status"] = "failed"
    with pytest.raises(ValueError, match="completed_census_required"):
        export_native_output(source, report, output)
    assert not output.exists()
