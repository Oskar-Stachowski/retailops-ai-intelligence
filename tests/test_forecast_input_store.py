import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline
from test_forecast_runtime import baseline, mocked_release
from test_forecast_runtime import prepared_input as prepared_input

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.execution_contracts import (
    ExecutionLimits,
    RuntimeExecution,
    RuntimeResult,
)
from retailops_ai.forecast_jobs.input_store import RegisteredInputs, RegistrationLimits
from retailops_ai.forecast_jobs.inputs import InputContent, prepared
from retailops_ai.forecast_jobs.preflight import result_report
from retailops_ai.forecast_jobs.queue import LeaseLost
from retailops_ai.forecast_jobs.supervisor import ExecutionError, supervise


def execution(inputs, **limits):
    """Mock qualification is used only in unit tests; no model enters an actual Registry."""
    release, pin, _ = mocked_release(inputs, baseline(inputs))
    return RuntimeExecution(
        environment="test",
        compose=False,
        inputs=inputs,
        release=release,
        runtime_pin=pin,
        limits=ExecutionLimits(**limits),
    )


def result(request, **updates):
    values = (1.0,) * len(request.inputs.rows)
    raw = dict(
        profile_id=request.inputs.profile_id,
        release_id=request.release.release_id,
        quantities=values,
        quantities_sha256=canonical_sha256(values),
        cold_load_seconds=0.01,
        compute_seconds=0.01,
        peak_rss_bytes=1024**2,
    )
    raw.update(updates)
    return RuntimeResult.model_validate_json(json.dumps(raw))


def replace_child(monkeypatch, script):
    from retailops_ai.forecast_jobs import supervisor

    original = subprocess.Popen
    children = []

    def spawn(command, **kwargs):
        assert command[1:] == ["-I", "-m", "retailops_ai.forecast_jobs.runtime_executor"]
        child = original([sys.executable, "-I", "-c", script], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(supervisor.subprocess, "Popen", spawn)
    return children


@pytest.mark.parametrize(
    "limits",
    [dict(max_profiles=257), dict(max_profiles=True), dict(max_storage_bytes=256 * 1024**2 + 1)],
)
def test_registration_limits_cannot_exceed_database_caps(limits):
    with pytest.raises(ValidationError):
        RegistrationLimits.model_validate_json(json.dumps(limits))


@pytest.mark.parametrize("change", ["checksum", "time"])
def test_registered_input_revalidates_full_content_and_database_clock(prepared_input, change):
    raw = dict(
        environment="test",
        inputs=prepared_input.model_dump(mode="json"),
        registered_at=datetime.now(UTC).isoformat(),
        storage_bytes=100000,
        profile_sha256=canonical_sha256(prepared_input.model_dump(mode="json")),
    )
    if change == "checksum":
        raw["profile_sha256"] = "d" * 64
    else:
        raw["registered_at"] = (prepared_input.as_of_time - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValidationError, match="integrity"):
        RegisteredInputs.model_validate_json(json.dumps(raw))


def test_supervisor_returns_bound_result_and_strips_credentials(prepared_input, monkeypatch):
    request = execution(prepared_input)
    expected = result(request)
    for name in (
        "DATABASE_URL",
        "AWS_SECRET_ACCESS_KEY",
        "API_AUTH_FILE",
        "MLFLOW_TRACKING_TOKEN",
        "PYTHONPATH",
        "HTTP_PROXY",
    ):
        monkeypatch.setenv(name, "private-unit-marker")
    script = (
        "import os,sys; "
        "assert not any(k.startswith(('AWS_', 'DATABASE_', 'MLFLOW_', 'API_')) for k in os.environ); "
        "assert 'PYTHONPATH' not in os.environ and 'HTTP_PROXY' not in os.environ; "
        "assert os.environ['OPENBLAS_NUM_THREADS']=='1'; "
        f"sys.stdout.buffer.write({expected.model_dump_json().encode()!r})"
    )
    children = replace_child(monkeypatch, script)
    assert supervise(request) == expected
    assert children[0].poll() == 0
    report = result_report(expected)
    assert "quantities" not in report and report["published_forecast_outputs"] == 0


@pytest.mark.parametrize("change", ["count", "profile", "release", "memory"])
def test_supervisor_rejects_wrong_result_pins_count_and_reported_peak(
    prepared_input, monkeypatch, change
):
    request = execution(prepared_input)
    updates = {}
    if change == "count":
        values = (1.0,) * (len(prepared_input.rows) - 1)
        updates.update(quantities=values, quantities_sha256=canonical_sha256(values))
    elif change in {"profile", "release"}:
        updates[change + "_id"] = (
            "batch-profile-sha256-" if change == "profile" else "model-release-sha256-"
        ) + "d" * 64
    else:
        updates["peak_rss_bytes"] = request.limits.rss_bytes + 1
    raw = result(request, **updates).model_dump_json().encode()
    replace_child(monkeypatch, f"import sys; sys.stdout.buffer.write({raw!r})")
    with pytest.raises(ExecutionError, match="pin_or_count"):
        supervise(request)


@pytest.mark.parametrize(
    "script,marker",
    [
        ("import time; time.sleep(5)", "wall_limit"),
        ("import sys; sys.stdout.buffer.write(b'x'*300000)", "output_limit"),
        ("import sys; sys.stderr.buffer.write(b'x'*20000)", "output_limit"),
        ("import sys; sys.exit(1)", "child_failed"),
    ],
)
def test_supervisor_bounds_wall_and_output_and_reaps_child(
    prepared_input, monkeypatch, script, marker
):
    request = execution(prepared_input, wall_seconds=0.4 if marker == "wall_limit" else 10.0)
    children = replace_child(monkeypatch, script)
    with pytest.raises(ExecutionError, match=marker):
        supervise(request)
    assert children[0].poll() is not None


def test_supervisor_enforces_actual_rss_and_kills_own_child(prepared_input, monkeypatch):
    request = execution(prepared_input, rss_bytes=128 * 1024**2, wall_seconds=10.0)
    children = replace_child(
        monkeypatch,
        "import time; value=bytearray(256*1024**2)\n"
        # calloc may reserve zero pages without making them resident on macOS.
        "for i in range(0,len(value),4096): value[i]=1\n"
        "time.sleep(30)",
    )
    with pytest.raises(ExecutionError, match="memory_limit"):
        supervise(request)
    assert children[0].poll() == -9


def test_supervisor_stops_after_lease_callback_refusal(prepared_input, monkeypatch):
    request = execution(prepared_input)
    children = replace_child(monkeypatch, "import time; time.sleep(30)")

    def lost():
        raise LeaseLost("batch_lease_lost")

    with pytest.raises(LeaseLost):
        supervise(request, on_tick=lost)
    assert children[0].poll() == -9


def test_real_isolated_executor_refuses_actual_lock_before_registry_io(prepared_input):
    raw = prepared_input.model_dump(mode="json", exclude={"profile_id"})
    manifest = raw["feature_manifest"]
    manifest["descriptor"]["code"]["dependency_lock_sha256"] = "d" * 64
    manifest["feature_set_id"] = "features-sha256-" + canonical_sha256(manifest["descriptor"])
    inputs = prepared(InputContent.model_validate_json(json.dumps(raw)))
    request = execution(inputs)
    response = subprocess.run(
        [sys.executable, "-I", "-m", "retailops_ai.forecast_jobs.runtime_executor"],
        input=request.model_dump_json().encode(),
        capture_output=True,
        timeout=10,
        check=False,
        env={"PATH": os.environ["PATH"], "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
    )
    assert (
        response.returncode == 2
        and response.stderr.strip() == b"forecast_runtime_dependency_lock_mismatch"
    )
    with pytest.raises(ExecutionError, match="child_failed"):
        supervise(request)
