"""V12 import projects dual targets and preserves provenance without qualifying serving."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.model_lifecycle import v12_evidence as v12
from retailops_ai.model_lifecycle import v12_mlflow as tracking
from retailops_ai.source_snapshot.files import file_hash


def write_fixture(root, status="not_ready"):
    """Small transport/mapping fixture; upstream semantic verification is separately delegated."""
    freeze_id = "functional-v12-freeze-sha256-" + "f" * 64
    campaign_id = "functional-v12-campaign-sha256-" + "c" * 64
    replay_id = "functional-v12-replay-sha256-" + "d" * 64
    model_status = "ready" if status == "passed" else "not_ready"
    binding = {
        "campaign_id": campaign_id,
        "replay_id": replay_id,
        "freeze_id": freeze_id,
        "forecast_model_status": model_status,
        "quality_qualification_status": status,
    }
    point = {"mae": 1.25, "mse": 2.5, "normalized_bias": None, "zero_actual_excess_units": 0}
    forecast = {
        "median": point,
        "mean": point | {"mae": 3.5, "mse": 14.0},
        "interval": {"coverage": 0.9, "mean_score": 8.0, "mean_width": 4.0},
    }
    metrics = {
        "status": status,
        "segment_counts": {"passed": 1, "failed": int(status != "passed")},
        "failed_reasons": {"mean_mse_regression": 1} if status != "passed" else {},
        "segments": [
            {
                "fold": "pooled",
                "role": "development_holdout",
                "dimension": "global",
                "candidate": forecast,
                "baseline": forecast,
            }
        ],
    }
    reports = {
        "handoff.json": {
            "version": v12.VERSION,
            **binding,
            "deployment": "not_promoted",
            "mlflow_or_registry_written": False,
            "independent_replay": "passed",
            "all_preregistered_cohorts_included": True,
            "ai05_import": {"legacy_single_point_import_compatible": False},
        },
        "model_card.json": {
            "failed_reasons": metrics["failed_reasons"],
            "times": {"training_started_at": None},
        },
        "signature.json": {
            "version": "forecast-functional-v12-signature-1.0.0",
            "deployable_service_contract": False,
            "output_schema": {
                "properties": {"median": {}, "mean": {}, "interval": {}},
                "required": ["median", "mean", "interval"],
            },
            "output_schema_scope": "each_of_candidate_and_baseline_in_returned_three_tuple",
            "return_tuple": [
                "candidate: FunctionalForecast",
                "baseline: FunctionalForecast",
                "metadata: dict",
            ],
            "outputs": {
                "candidate": {
                    "median": "MAE target; exact selected local baseline median",
                    "mean": "MSE and normalized bias target; frozen mean policy",
                    "interval": "central 90%; exact selected local baseline interval",
                },
                "baseline": "separate retained median, mean and interval reference on identical keys",
                "metadata": "recipe_id, calibration cell, mean source and exact-reference flags",
            },
        },
        "input_example.json": {"prediction_executed": False, "input": {"actual": None}},
        "campaign/freeze.json": {
            "freeze_id": freeze_id,
            "descriptor": {
                "remote_preparation": {"source_commit": "b" * 40},
                "quality_policy": {"version": "forecast-quality-2.0.0"},
            },
        },
        "campaign/campaign_manifest.json": {"campaign_id": campaign_id},
        "campaign/metrics.json": metrics,
        "replay/replay_receipt.json": {"replay_id": replay_id},
        "checkpoints/seed-1/checkpoint_manifest.json": {"seed": 1},
    }
    root.mkdir()
    for name, value in reports.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(canonical_bytes(value) + b"\n")
    (root / "checkpoints/seed-1/payload.tar.gz").write_bytes(b"explicit-test-checkpoint-bytes")
    names = [*reports, "checkpoints/seed-1/payload.tar.gz"]
    files = {
        name: {"size_bytes": file_hash(root, name)[0], "sha256": file_hash(root, name)[1]}
        for name in names
    }
    descriptor = {
        "version": v12.VERSION,
        **binding,
        "ai_code_commit": "a" * 40,
        "code": {"code_sha256": "e" * 64, "dependency_lock_sha256": "0" * 64},
        "checkpoints": {"1": "checkpoints/seed-1"},
        "files": files,
        "bytes": sum(ref["size_bytes"] for ref in files.values()),
    }
    manifest = {
        "run_id": "functional-v12-run-sha256-" + canonical_sha256(descriptor),
        "descriptor": descriptor,
        "exported_at": "2026-09-30T20:00:00+00:00",
    }
    (root / "run_manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    return root


def verifier_receipt(root):
    manifest = json.loads((root / "run_manifest.json").read_text())
    return {
        "status": "passed",
        "run_id": manifest["run_id"],
        "run_manifest_sha256": file_hash(root, "run_manifest.json")[1],
        "verifier_code_sha256": manifest["descriptor"]["code"]["code_sha256"],
        "source_generation": False,
        "model_refits": 0,
        "package_file": "installed-test-wheel",
    }


@pytest.mark.parametrize("size", [0, 524 * 1024**2, v12.MAX_BYTES])
def test_v12_receipts_accept_checkpoint_sizes_and_empty_files_without_allocating(size):
    receipt = v12.V12ArtifactReceipt(size_bytes=size, sha256="a" * 64)
    assert receipt.size_bytes == size


@pytest.mark.parametrize("size", [-1, v12.MAX_BYTES + 1, True, 1.0])
def test_v12_receipts_reject_invalid_sizes(size):
    with pytest.raises(ValueError):
        v12.V12ArtifactReceipt(size_bytes=size, sha256="a" * 64)


def test_actual_v12_signature_shape_keeps_candidate_and_reference(evidence):
    assert isinstance(evidence.signature["outputs"]["baseline"], str)
    assert set(evidence.signature["output_schema"]["properties"]) == {"median", "mean", "interval"}


@pytest.mark.parametrize("mutation", ["baseline_dict", "single_point", "candidate_only"])
def test_collapsed_or_incompatible_output_signatures_are_rejected(tmp_path, monkeypatch, mutation):
    root = write_fixture(tmp_path / "export")
    signature = json.loads((root / "signature.json").read_text())
    if mutation == "baseline_dict":
        signature["outputs"]["baseline"] = {"median": "units", "mean": "units", "interval": "units"}
    elif mutation == "single_point":
        signature["output_schema"]["properties"] = {"prediction": {}}
    else:
        signature["return_tuple"] = ["candidate: FunctionalForecast"]
    (root / "signature.json").write_bytes(canonical_bytes(signature) + b"\n")
    manifest = json.loads((root / "run_manifest.json").read_text())
    size, digest = file_hash(root, "signature.json")
    manifest["descriptor"]["files"]["signature.json"] = {"size_bytes": size, "sha256": digest}
    manifest["descriptor"]["bytes"] = sum(
        ref["size_bytes"] for ref in manifest["descriptor"]["files"].values()
    )
    manifest["run_id"] = "functional-v12-run-sha256-" + canonical_sha256(manifest["descriptor"])
    (root / "run_manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    monkeypatch.setattr(v12, "verify_with_wheel", lambda root, *_: verifier_receipt(root))
    with pytest.raises(ValueError, match="dual_target_binding"):
        v12.load_evidence(root, Path("/operator-selected/python"))


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root = write_fixture(tmp_path / "export")
    monkeypatch.setattr(v12, "verify_with_wheel", lambda root, *_: verifier_receipt(root))
    return v12.load_evidence(root, Path("/operator-selected/python"))


class MemoryTracking:
    """MLflow transport double retaining every artifact and recorded state transition."""

    def __init__(self):
        self.runs, self.artifacts, self.calls = [], {}, []
        self.corrupt = False

    def api(self, path, payload=None):
        self.calls.append((path, deepcopy(payload)))
        if "get-by-name" in path:
            return {"experiment": {"experiment_id": "1"}}
        if path.endswith("/search"):
            return {"runs": deepcopy(self.runs)}
        if path.endswith("/create"):
            run = {
                "info": {
                    "run_id": str(len(self.runs) + 1),
                    "status": "RUNNING",
                    "artifact_uri": "mlflow-artifacts:/1/run/artifacts",
                },
                "data": {"tags": payload["tags"], "params": [], "metrics": []},
            }
            self.runs.append(run)
            return {"run": deepcopy(run)}
        run = self.runs[int(payload["run_id"]) - 1]
        if path.endswith("/log-batch"):
            run["data"]["params"] = payload["params"]
            run["data"]["metrics"] = payload["metrics"]
        elif path.endswith("/set-tag"):
            tags = {t["key"]: t["value"] for t in run["data"]["tags"]}
            tags[payload["key"]] = payload["value"]
            run["data"]["tags"] = [{"key": key, "value": value} for key, value in tags.items()]
        elif path.endswith("/update"):
            run["info"]["status"] = payload["status"]
        else:
            raise AssertionError("unexpected operation " + path)
        return {}

    def upload(self, path, root, name, receipt):
        raw = (root / name).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (receipt.size_bytes, receipt.sha256)
        self.artifacts[path] = raw + b"corrupt" if self.corrupt else raw

    def checksum(self, path, maximum):
        raw = self.artifacts[path]
        return len(raw), hashlib.sha256(raw).hexdigest()


def test_complete_import_keeps_all_original_bytes_separate_metrics_and_export_time(
    evidence, tmp_path
):
    client = MemoryTracking()
    result = tracking.import_evidence(evidence, client, tmp_path / "work")
    assert result["status"] == "imported" and result["forecast_model_status"] == "not_ready"
    run = client.runs[0]
    tags = {t["key"]: t["value"] for t in run["data"]["tags"]}
    assert tags["retailops.exported_at"] == "2026-09-30T20:00:00+00:00"
    assert tags["retailops.imported_at"] != tags["retailops.exported_at"]
    assert tags["retailops.serving_eligible"] == tags["retailops.promotion_eligible"] == "false"
    assert tags["retailops.registration_eligible"] == "false"
    metrics = {m["key"]: m["value"] for m in run["data"]["metrics"]}
    assert metrics["pooled.development_holdout.candidate.median.mae"] == 1.25
    assert metrics["pooled.development_holdout.candidate.mean.mse"] == 14.0
    assert metrics["pooled.development_holdout.candidate.interval.coverage"] == 0.9
    assert not any(key.endswith("normalized_bias") for key in metrics)
    for name in evidence.files:
        assert (
            client.artifacts[tracking.artifact_path(run, name)]
            == (evidence.root / name).read_bytes()
        )
    assert evidence.card["times"]["training_started_at"] is None
    assert len(list((tmp_path / "work").iterdir())) == 1  # lock only; no duplicate large archive
    assert not any(
        "registered-model" in path or "model-version" in path for path, _ in client.calls
    )


def test_verified_repeat_checks_every_remote_file_and_does_not_create_second_run(
    evidence, tmp_path
):
    client = MemoryTracking()
    work = tmp_path / "work"
    tracking.import_evidence(evidence, client, work)
    assert tracking.import_evidence(evidence, client, work)["status"] == "already_imported"
    assert len(client.runs) == 1
    path = tracking.artifact_path(client.runs[0], "checkpoints/seed-1/payload.tar.gz")
    client.artifacts[path] += b"remote-tamper"
    with pytest.raises(ValueError, match="remote_checksum"):
        tracking.import_evidence(evidence, client, work)
    assert len(client.runs) == 1


def test_upload_failure_is_retained_and_never_automatically_retried(evidence, tmp_path):
    client = MemoryTracking()
    client.corrupt = True
    work = tmp_path / "work"
    with pytest.raises(ValueError, match="remote_checksum"):
        tracking.import_evidence(evidence, client, work)
    assert client.runs[0]["info"]["status"] == "FAILED"
    client.corrupt = False
    with pytest.raises(ValueError, match="existing_import_conflict"):
        tracking.import_evidence(evidence, client, work)
    assert len(client.runs) == 1


def test_local_tamper_fails_before_any_tracking_write(evidence, tmp_path):
    (evidence.root / "signature.json").write_text("{}")
    client = MemoryTracking()
    with pytest.raises(ValueError, match="export_changed"):
        tracking.import_evidence(evidence, client, tmp_path / "work")
    assert client.calls == []


def test_ready_quality_still_does_not_authorize_serving(tmp_path, monkeypatch):
    root = write_fixture(tmp_path / "export", "passed")
    monkeypatch.setattr(v12, "verify_with_wheel", lambda root, *_: verifier_receipt(root))
    ready = v12.load_evidence(root, Path("/operator-selected/python"))
    client = MemoryTracking()
    result = tracking.import_evidence(ready, client, tmp_path / "work")
    assert result["forecast_model_status"] == "ready" and result["serving_eligible"] is False


def test_wrong_verifier_binding_and_partial_exports_are_rejected(tmp_path, monkeypatch):
    root = write_fixture(tmp_path / "export")
    wrong = verifier_receipt(root) | {"verifier_code_sha256": "1" * 64}
    monkeypatch.setattr(v12, "verify_with_wheel", lambda *_: wrong)
    with pytest.raises(ValueError, match="verifier_binding"):
        v12.load_evidence(root, Path("/operator-selected/python"))
    (root / "checkpoints/seed-1/payload.tar.gz").unlink()
    with pytest.raises(ValueError):
        v12.load_evidence(root, Path("/operator-selected/python"))


def test_verifier_runs_isolated_without_credentials_and_rejects_failures(tmp_path, monkeypatch):
    calls = []

    def invoke(command, **kwargs):
        calls.append((command, kwargs))
        kwargs["stdout"].write(canonical_bytes({"status": "passed"}))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-never-forwarded")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    monkeypatch.setattr(v12.subprocess, "run", invoke)
    assert v12.verify_with_wheel(tmp_path, Path(sys.executable))["status"] == "passed"
    command, kwargs = calls[0]
    assert command[1:4] == ["-I", "-B", "-c"]
    assert set(kwargs["env"]) == {"PYTHONDONTWRITEBYTECODE", "TMPDIR"}
    assert kwargs["timeout"] == 3600 and kwargs["stdin"] == subprocess.DEVNULL
    monkeypatch.setattr(
        v12.subprocess, "run", lambda command, **_: subprocess.CompletedProcess(command, 1)
    )
    with pytest.raises(ValueError, match="pinned_verifier_failed"):
        v12.verify_with_wheel(tmp_path, Path(sys.executable))


def test_actual_child_loads_only_operator_installed_verifier(tmp_path, monkeypatch):
    """Exercise the process boundary with an explicit installed verifier test double."""
    root = write_fixture(tmp_path / "export")
    environment = tmp_path / "verifier-env"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True)
    site = (
        environment
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    package = site / "retailops_ai"
    (package / "forecasting").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "forecasting/__init__.py").write_text("")
    (package / "forecasting/functional_v12_run.py").write_text(
        "import json,os\n"
        "def verify_run(root):\n"
        '    assert "AWS_SECRET_ACCESS_KEY" not in os.environ\n'
        '    assert "PYTHONPATH" not in os.environ\n'
        '    return json.loads((root/"run_manifest.json").read_text())\n'
    )
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-forwarded")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "untrusted"))
    result = v12.load_evidence(root, environment / "bin/python")
    assert result.verifier["package_file"] == str(package / "__init__.py")
    assert not list(package.rglob("*.pyc"))
    assert result.run_id == verifier_receipt(root)["run_id"]


def test_actual_child_rejects_current_unpinned_interpreter(tmp_path):
    root = write_fixture(tmp_path / "export")
    with pytest.raises(ValueError, match="pinned_verifier_failed"):
        v12.verify_with_wheel(root, Path(sys.executable))


@pytest.mark.parametrize("target", ["params", "metrics", "tags"])
def test_repeat_rejects_changed_tracking_metadata(evidence, tmp_path, target):
    client = MemoryTracking()
    work = tmp_path / "work"
    tracking.import_evidence(evidence, client, work)
    entry = client.runs[0]["data"][target][0]
    entry["value"] = 1000.0 if target == "metrics" else "tampered"
    with pytest.raises(ValueError, match="existing_import_conflict"):
        tracking.import_evidence(evidence, client, work)
    assert len(client.runs) == 1


def test_unknown_failed_status_is_not_hidden(evidence, tmp_path):
    class BrokenStatus(MemoryTracking):
        def api(self, path, payload=None):
            if path.endswith("/update") and payload["status"] == "FAILED":
                raise OSError("database unavailable")
            return super().api(path, payload)

    client = BrokenStatus()
    client.corrupt = True
    with pytest.raises(ValueError, match="failed_status_unknown"):
        tracking.import_evidence(evidence, client, tmp_path / "work")


def test_no_tracking_write_for_in_memory_projection_tamper_or_overlapping_work(evidence):
    client = MemoryTracking()
    with pytest.raises(ValueError, match="work_overlaps_export"):
        tracking.import_evidence(evidence, client, evidence.root / "work")
    assert not (evidence.root / "work").exists()
    evidence.handoff["forecast_model_status"] = "ready"
    with pytest.raises(ValueError, match="export_changed"):
        tracking.import_evidence(evidence, client, evidence.root / "work")
    assert client.calls == []


@pytest.mark.parametrize(
    "uri", ["file:/private", "https://example.org", "mlflow-artifacts:/../bad"]
)
def test_invalid_artifact_destinations_are_rejected(uri):
    with pytest.raises(ValueError):
        tracking.artifact_path({"info": {"artifact_uri": uri}}, "signature.json")
