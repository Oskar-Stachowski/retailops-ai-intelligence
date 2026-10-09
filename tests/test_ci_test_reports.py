"""Execution receipts must prove every collected test ran once before timing reuse."""

import hashlib
import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.update_ci_test_timings import validated_timings

ROOT = Path(__file__).resolve().parents[1]


def receipts():
    full = [f"tests/test_{i}.py::test_value" for i in range(4)]
    digest = hashlib.sha256("\n".join(full).encode()).hexdigest()
    return [
        {
            "version": "ci-test-execution-1.0.0",
            "run_id": "fixture",
            "run_attempt": "1",
            "commit": "a" * 40,
            "shard": i,
            "shards": 4,
            "exit_code": 0,
            "full_collection_sha256": digest,
            "full_node_ids": full,
            "selected_node_ids": [node],
            "phases": {
                node: {
                    phase: {"seconds": 0.25, "outcome": "passed"}
                    for phase in ("setup", "call", "teardown")
                }
            },
            "seconds": {node.split("::")[0]: 0.75},
        }
        for i, node in enumerate(full)
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "duplicate",
        "failed",
        "mixed-commit",
        "mixed-run",
        "mixed-attempt",
        "unexecuted",
        "no-call",
        "no-teardown",
        "bad-time",
        "forged-time",
        "overlap",
        "extra-test",
        "in-progress",
        "in-progress-zero-exit",
    ],
)
def test_incomplete_or_inconsistent_receipts_cannot_publish_weights(mutation):
    reports = deepcopy(receipts())
    node = reports[0]["selected_node_ids"][0]
    if mutation == "missing":
        reports.pop()
    elif mutation == "duplicate":
        reports[-1] = reports[0]
    elif mutation == "failed":
        reports[0]["exit_code"] = 1
    elif mutation.startswith("mixed-"):
        key = {"mixed-commit": "commit", "mixed-run": "run_id", "mixed-attempt": "run_attempt"}[
            mutation
        ]
        reports[0][key] = "different"
    elif mutation == "unexecuted":
        reports[0]["phases"] = {}
    elif mutation in {"no-call", "no-teardown"}:
        reports[0]["phases"][node].pop(mutation.removeprefix("no-"))
    elif mutation == "bad-time":
        reports[0]["phases"][node]["call"]["seconds"] = float("nan")
    elif mutation == "forged-time":
        reports[0]["seconds"][node.split("::")[0]] = 100
    elif mutation == "overlap":
        reports[1]["selected_node_ids"] = [node]
    elif mutation.startswith("in-progress"):
        reports[0]["reporting_state"] = "in_progress"
        reports[0]["exit_code"] = None if mutation == "in-progress" else 0
    else:
        reports[0]["selected_node_ids"].append("tests/test_hidden.py::test_hidden")
    with pytest.raises(ValueError):
        validated_timings(reports)


def test_phase_time_includes_setup_and_teardown():
    result = validated_timings(receipts())
    assert result["seconds"] == {f"tests/test_{i}.py": 0.75 for i in range(4)}


def test_retry_reuses_successful_shards_but_never_hides_new_failure():
    reports = receipts()
    failed = deepcopy(reports[0])
    reports[0]["run_attempt"] = "2"
    failed["exit_code"] = 1
    assert validated_timings([*reports, failed])["measured_attempts"]["0"] == "2"
    latest = deepcopy(reports[0])
    latest.update(run_attempt="3", exit_code=1)
    with pytest.raises(ValueError, match="failed_shard"):
        validated_timings([*reports, failed, latest])


def test_real_pytest_shards_report_collection_calls_and_skips(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    for i in range(4):
        (tests / f"test_{i}.py").write_text(
            "import pytest\ndef test_pass():\n    assert 1 + 1 == 2\n"
            "@pytest.mark.skip(reason='explicit fixture')\ndef test_skip():\n    assert False\n"
        )
    (tmp_path / "weights.json").write_text('{"seconds":{}}')
    code = """
import sys
from pathlib import Path
import pytest
sys.path.insert(0, sys.argv[1])
from scripts import run_ci_tests as runner
runner.ROOT = Path(sys.argv[2])
runner.TIMINGS = runner.ROOT / 'weights.json'
plugin = runner.Shard(int(sys.argv[3]), 4, None, runner.ROOT / (sys.argv[3] + '.json'))
raise SystemExit(pytest.main([str(runner.ROOT / 'tests'), '--rootdir=' + str(runner.ROOT), '-q'], plugins=[plugin]))
"""
    for i in range(4):
        result = subprocess.run(
            [sys.executable, "-c", code, str(ROOT), str(tmp_path), str(i)],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        )
        assert result.returncode == 0, result.stdout + result.stderr
    result = validated_timings([json.loads((tmp_path / f"{i}.json").read_text()) for i in range(4)])
    assert set(result["seconds"]) == {f"tests/test_{i}.py" for i in range(4)}


def test_abruptly_stopped_pytest_preserves_completed_file_without_success(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("def test_complete():\n    assert 2 + 2 == 4\n")
    (tests / "test_b.py").write_text(
        "import time\nfrom pathlib import Path\n"
        "def test_wait():\n"
        f"    Path({str(tmp_path / 'started')!r}).write_text('started')\n"
        "    time.sleep(60)\n"
    )
    (tmp_path / "weights.json").write_text('{"seconds":{}}')
    code = """
import sys
from pathlib import Path
import pytest
sys.path.insert(0, sys.argv[1])
from scripts import run_ci_tests as runner
runner.ROOT = Path(sys.argv[2])
runner.TIMINGS = runner.ROOT / 'weights.json'
plugin = runner.Shard(0, 1, None, runner.ROOT / 'receipt.json')
raise SystemExit(pytest.main([str(runner.ROOT / 'tests'), '--rootdir=' + str(runner.ROOT), '-q'], plugins=[plugin]))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(ROOT), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "started").exists():
            assert process.poll() is None, process.communicate(timeout=5)
            assert time.monotonic() < deadline, "controlled pytest did not start"
            time.sleep(0.02)
        process.kill()
        process.communicate(timeout=5)
        report = json.loads((tmp_path / "receipt.json").read_text())
        assert report["exit_code"] is None
        assert report["reporting_state"] == "in_progress"
        assert len(report["selected_node_ids"]) == 2
        phases = report["phases"]["tests/test_a.py::test_complete"]
        assert set(phases) == {"setup", "call", "teardown"}
        assert all(phase["outcome"] == "passed" for phase in phases.values())
        assert not list(tmp_path.glob("receipt.json.*.tmp"))
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_failed_atomic_replacement_preserves_previous_receipt(tmp_path, monkeypatch):
    from scripts import run_ci_tests as runner

    path = tmp_path / "receipt.json"
    plugin = runner.Shard(0, 1, None, path)
    plugin._write_timings(None)
    before = path.read_bytes()

    def fail_replace(source, destination):
        assert destination == path
        raise OSError("controlled replace failure")

    monkeypatch.setattr(runner.os, "replace", fail_replace)
    with pytest.raises(OSError, match="controlled replace failure"):
        plugin._write_timings(0)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("receipt.json.*.tmp"))
