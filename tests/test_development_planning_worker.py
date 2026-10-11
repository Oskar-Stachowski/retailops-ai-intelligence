"""Isolated interpreter/environment boundaries using explicitly fake producer modules."""

import hashlib
import json
import subprocess
import sys

import pytest

from retailops_ai.evaluation_campaign import development_planning_worker as worker
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write


def worker_case(tmp_path):
    source, root, raw = (tmp_path / name for name in ("producer", "attempt", "raw"))
    root.mkdir()
    raw.mkdir()
    for package in ("data", "data/generator", "data/inventory", "data/anomalies", "services/api"):
        p = source / package
        p.mkdir(parents=True, exist_ok=True)
        (p / "__init__.py").write_text("")
    (source / "services/api/requirements.txt").write_text("")
    (source / "data/requirements-parquet.txt").write_text("")
    provenance = {"git_commit": "a" * 40, "code_state": "clean", "dependency_sha256": "b" * 64}
    (source / "data/inventory/source_dataset_io.py").write_text("def fingerprint(): return {}\n")
    (source / "data/generator/identity.py").write_text(
        f"def code_provenance(fingerprint): return {provenance!r}\n"
    )
    (source / "data/generator/configuration.py").write_text(
        "class DatasetGenerationConfig:\n"
        " def __init__(self, **kwargs): self.values=kwargs\n"
        " def parameters(self): return self.values\n"
        "def resolve_generation_config(value): return value\n"
    )
    native = source / "data/anomalies/development_plan.py"
    native.write_text(
        "VERSION='ai09-native-development-scenario-selection-1.0.0'\n"
        "class DevelopmentScenarioRecipe:\n"
        " @classmethod\n"
        " def model_validate(cls, value): return value\n"
        "def prepare_development_scenarios(directory, generation, recipe):\n"
        " (directory/'opened').write_text('controlled source read')\n"
        f" return {{'source_provenance': {provenance!r}, 'recipe': recipe}}\n"
    )
    request = {
        "source": {
            "producer_commit": "a" * 40,
            "producer_lock_sha256": "b" * 64,
            "exporter_lock_sha256": hashlib.sha256(b"").hexdigest(),
        },
        "generation": {"requested_parameters": {}, "resolved_parameters": {}},
        "planning": {
            "producer_planner_sha256": hashlib.sha256(native.read_bytes()).hexdigest(),
            "producer_planner_version": "ai09-native-development-scenario-selection-1.0.0",
            "max_bundle_bytes": 2 * 1024**2,
        },
        "selection": {"controlled": True},
        "raw_source": str(raw),
    }
    return source, root, raw, request


@pytest.mark.parametrize("changed", [None, "planner", "lock", "provenance", "version"])
def test_isolated_worker_checks_environment_and_exact_module_before_native_read(tmp_path, changed):
    source, root, raw, request = worker_case(tmp_path)
    if changed == "planner":
        request["planning"]["producer_planner_sha256"] = "0" * 64
    if changed == "lock":
        request["source"]["exporter_lock_sha256"] = "0" * 64
    if changed == "provenance":
        request["source"]["producer_commit"] = "0" * 40
    if changed == "version":
        request["planning"]["producer_planner_version"] = "wrong"
    write(root / "request.json", request)
    result = subprocess.run(  # noqa: S603 -- fixed owned worker and controlled local fixture
        [sys.executable, "-I", "-B", worker.__file__, str(source), str(root)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if changed:
        assert result.returncode != 0 and not (raw / "opened").exists()
        assert not (root / "native-plans.json").exists()
        assert "mismatch" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert (raw / "opened").read_text() == "controlled source read"
        assert read(root / "result.json")["worker_peak_rss_bytes"] > 0
        assert json.loads((root / "native-plans.json").read_bytes())["recipe"] == {
            "controlled": True
        }
        assert (root / "native-plans.json").stat().st_mode & 0o777 == 0o600
