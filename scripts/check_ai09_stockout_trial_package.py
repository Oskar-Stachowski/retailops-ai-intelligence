"""Run complete source and installed stockout fixture controls in an isolated runner.

These are synthetic role/metadata fixtures with real numerical fits and cold
workers, not a Project campaign or a capacity/quality qualification.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from measure_ai09_development_capacity import git, monitor

ROOT = Path(__file__).resolve().parents[1]
GROUPS = (
    ("tests/test_campaign_stockout_trial.py",),
    (
        "tests/test_campaign_stockout_training.py",
        "tests/test_campaign_stockout_calibration.py",
        "tests/test_campaign_stockout_preparation.py",
        "tests/test_campaign_export.py",
        "tests/test_campaign_generation.py",
        "tests/test_campaign_role_protocol.py",
        "tests/test_campaign_worker_import_boundary.py",
        "tests/test_ai09_campaign_journal.py",
    ),
)


def pins() -> dict[str, str]:
    return {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for top in ("src", "scripts", "tests")
        for p in (ROOT / top).rglob("*.py")
    }


def cases(path: Path) -> set[tuple[str, str]]:
    document = ET.parse(path)  # noqa: S314 -- JUnit written by our just-completed pytest child
    suite = document.getroot().find("testsuite")
    if suite is None or any(int(suite.attrib[k]) for k in ("failures", "errors", "skipped")):
        raise ValueError("stockout_package_incomplete_test_suite")
    result = {(t.attrib["classname"], t.attrib["name"]) for t in document.iter("testcase")}
    if not result or len(result) != int(suite.attrib["tests"]):
        raise ValueError("stockout_package_missing_or_duplicate_test_case")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    measured = output / "measurements"
    measured.mkdir(mode=0o700)
    temporary = output / "tmp"
    temporary.mkdir(mode=0o700)
    original = pins()
    head = git(ROOT, "rev-parse", "HEAD")
    if git(ROOT, "diff", "HEAD", "--name-only"):
        raise ValueError("stockout_package_clean_checkout_required")
    budgets: dict[str, int | float] = {
        "tree_rss_bytes": 512 * 1024**2,
        "scratch_bytes": 256 * 1024**2,
        "minimum_available_memory_bytes": 1024**3,
        "minimum_free_disk_bytes": 6 * 1024**3,
        "sample_seconds": 0.2,
    }
    deadline = time.perf_counter() + 1200
    report: dict[str, Any] = {
        "scope": "complete_synthetic_fixture_controls_not_Project_or_quality_acceptance",
        "head": head,
        "status": "failed",
        "budgets": budgets,
        "scratch_measurement": "measurement logs only; test/dependency scratch excluded; global disk reserve enforced",
        "new_project_operations": 0,
        "stage_ready": False,
        "phases": {},
    }

    def run(name: str, command: list[str], *, installed: bool = False) -> None:
        if pins() != original or git(ROOT, "rev-parse", "HEAD") != head:
            raise ValueError("stockout_package_checkout_changed")
        env = dict(
            os.environ,
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONNOUSERSITE="1",
            PYTHONPATH=str(output / "installed" if installed else ROOT / "src"),
            TMPDIR=str(temporary),
            OMP_NUM_THREADS="1",
            OPENBLAS_NUM_THREADS="1",
            MKL_NUM_THREADS="1",
        )
        print(json.dumps({"phase": name, "status": "started"}), flush=True)
        result = monitor(
            command,
            cwd=ROOT,
            env=env,
            log=measured / (name + ".log"),
            roots=(measured,),
            budgets=budgets,
            deadline=deadline,
        )
        result["code_pins_unchanged"] = pins() == original
        report["phases"][name] = result
        (measured / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"phase": name, **result}), flush=True)
        if result["status"] != "passed" or not result["code_pins_unchanged"]:
            raise ValueError("stockout_package_phase_failed_" + name)

    try:
        inventories: dict[str, set[tuple[str, str]]] = {}
        for kind in ("source", "installed"):
            if kind == "installed":
                uv = shutil.which("uv")
                if uv is None:
                    raise ValueError("stockout_package_uv_unavailable")
                run("build", [uv, "build", "--wheel", "--out-dir", str(output / "dist")])
                wheels = list((output / "dist").glob("*.whl"))
                if len(wheels) != 1:
                    raise ValueError("stockout_package_single_wheel_required")
                wheel = wheels[0]
                run(
                    "install",
                    [
                        uv,
                        "pip",
                        "install",
                        "--no-deps",
                        "--python",
                        sys.executable,
                        "--target",
                        str(output / "installed"),
                        str(wheel),
                    ],
                )
                with zipfile.ZipFile(wheel) as archive:
                    mapping = {
                        str(p.relative_to(ROOT / "src")): p
                        for p in (ROOT / "src/retailops_ai").rglob("*.py")
                    }
                    modules = len(mapping)
                    for version in range(33, 53):
                        for p in (ROOT / "contracts/evaluation" / f"v{version}").glob("*.json"):
                            mapping[f"retailops_ai/evaluation_campaign/v{version}/{p.name}"] = p
                    for relative, path in mapping.items():
                        if not (
                            path.read_bytes()
                            == archive.read(relative)
                            == (output / "installed" / relative).read_bytes()
                        ):
                            raise ValueError("stockout_package_byte_mismatch")
                    if any(".local/" in n or "/tests/" in n for n in archive.namelist()):
                        raise ValueError("stockout_package_private_file_in_wheel")
                report["package"] = {
                    "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                    "wheel_bytes": wheel.stat().st_size,
                    "python_modules_equal": modules,
                    "contract_files_equal": len(mapping) - modules,
                }
                run(
                    "installed-origin",
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        "import sys; from pathlib import Path; "
                        "from retailops_ai.evaluation_campaign import campaign_stockout_trial as m; "
                        "assert Path(m.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())",
                        str(output / "installed"),
                    ],
                    installed=True,
                )
            inventory: set[tuple[str, str]] = set()
            for index, group in enumerate(GROUPS):
                name = f"{kind}-{index}"
                junit = output / (name + ".xml")
                run(
                    name,
                    [
                        sys.executable,
                        "-B",
                        "-m",
                        "pytest",
                        "-x",
                        "-q",
                        *group,
                        "--junitxml=" + str(junit),
                    ],
                    installed=kind == "installed",
                )
                selected = cases(junit)
                if inventory & selected:
                    raise ValueError("stockout_package_overlapping_test_groups")
                inventory.update(selected)
            inventories[kind] = inventory
            report[kind + "_tests"] = len(inventory)
        if inventories["source"] != inventories["installed"] or pins() != original:
            raise ValueError("stockout_package_suite_or_checkout_mismatch")
        report.update(status="passed", same_test_inventory=True, code_pins_unchanged=True)
    finally:
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
