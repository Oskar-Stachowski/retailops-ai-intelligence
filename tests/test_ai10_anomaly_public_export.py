"""The native acceptance exporter cannot publish its private operator files or overwrite evidence."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_anomaly_oci as acceptance_runner  # noqa: E402
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


@pytest.mark.parametrize(
    "change", ["project", "service", "stopped", "missing", "public", "extra", "zero"]
)
def test_original_database_binding_refuses_foreign_or_unbounded_service(monkeypatch, change):
    project = "retailops_ai_anomaly_" + "a" * 10
    info = {
        "Config": {
            "Labels": {"com.docker.compose.project": project, "com.docker.compose.service": "db"}
        },
        "State": {"Running": True},
        "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "54321"}]}},
    }
    changed = copy.deepcopy(info)
    if change in {"project", "service"}:
        changed["Config"]["Labels"]["com.docker.compose." + change] = "foreign"
    elif change == "stopped":
        changed["State"]["Running"] = False
    elif change == "missing":
        changed["NetworkSettings"]["Ports"] = {}
    elif change == "public":
        changed["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostIp"] = "0.0.0.0"  # noqa: S104 - negative fixture; this mapping must be rejected
    elif change == "extra":
        changed["NetworkSettings"]["Ports"]["5432/tcp"].append(
            {"HostIp": "127.0.0.1", "HostPort": "54322"}
        )
    else:
        changed["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"] = "0"
    state = [info]
    monkeypatch.setattr(
        acceptance_runner,
        "command",
        lambda args: json.dumps(state) if "inspect" in args else "b" * 64,
    )
    assert (
        acceptance_runner.original_database_binding("unused-docker", ["unused-compose"], project)
        == "127.0.0.1:54321"
    )
    state[:] = [changed]
    with pytest.raises(ValueError, match="anomaly_original_database_"):
        acceptance_runner.original_database_binding("unused-docker", ["unused-compose"], project)


@pytest.mark.parametrize("export_owned", [True, False])
def test_late_failure_cannot_leave_own_full_acceptance_passed_or_overwrite_another_export(
    public_acceptance, monkeypatch, export_owned
):
    source, report, output = public_acceptance
    export_native_output(source, report, output)
    original = (output / "acceptance.json").read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_anomaly_oci.py", "--producer", "unused", "--native-output", str(output)],
    )

    def fail(args):
        args.native_export_created = export_owned
        raise ValueError("anomaly_original_database_loopback_required")

    monkeypatch.setattr(acceptance_runner, "run", fail)
    assert acceptance_runner.main() == 2
    if export_owned:
        failed = json.loads((output / "acceptance.json").read_bytes())
        assert failed["status"] == "failed"
        assert failed["failure_category"] == "anomaly_original_database_loopback_required"
        assert (output / "native-batch-output.json").read_bytes() == (
            source / "native-batch-output.json"
        ).read_bytes()
    else:
        assert (output / "acceptance.json").read_bytes() == original
