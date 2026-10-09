"""Isolated preparation worker and actual-runtime inspection; no Project authority.

Only stdlib is imported at module load so Source uses its own interpreter and
dependencies. The controller binds these bytes and the complete loaded code
trees before it accepts a native completion witness.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import resource
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("preparation_worker_module_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def runtime(root: Path, *, producer: bool) -> dict[str, Any]:
    """Pin actual code and installed distribution metadata, not just a lock filename."""
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("preparation_runtime_absolute_unlinked_root_required")
    code: dict[str, str] = {}
    for prefix in ("data", "ml") if producer else ("src/retailops_ai", "scripts"):
        for path in sorted((root / prefix).rglob("*.py")):
            if "__pycache__" not in path.parts:
                if path.is_symlink() or not path.is_file():
                    raise ValueError("preparation_runtime_regular_code_required")
                code[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    if not code:
        raise ValueError("preparation_runtime_code_missing")
    packages = [
        {
            "name": distribution.metadata["Name"],
            "version": distribution.version,
            "record_sha256": hashlib.sha256(
                (distribution.read_text("RECORD") or "").encode()
            ).hexdigest(),
        }
        for distribution in importlib.metadata.distributions()
    ]
    return {
        "version": "ai09-preparation-runtime-1.0.0",
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "code_sha256": digest(code),
        "code_files": code,
        "packages": sorted(packages, key=lambda item: (item["name"], item["version"])),
        "worker_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "distribution_RECORD_metadata_not_full_installed_file_verification": True,
    }


def native_witness(phase: str, result: dict[str, Any], identity: dict[str, Any]) -> dict[str, Any]:
    probe = load(ROOT / "scripts/measure_ai09_development_capacity.py", "capacity_worker_probe")
    witness = load(
        ROOT / "src/retailops_ai/evaluation_campaign/preparation_witness.py", "native_witness"
    )
    directory = Path(result["directory" if phase in probe.PHASES[:3] else "destination"])
    if phase == "generation":
        manifest = probe.read(directory / "dataset_manifest.v2.json")
        output, inputs = manifest["dataset_id"], []
        reports = ["dataset_manifest.v2.json", "source_report.json"]
    elif phase == "qualification":
        manifest = probe.read(directory / "qualification_manifest.json")
        output = manifest["qualification_id"]
        inputs = [manifest["descriptor"]["parent_source_id"]]
        reports = ["qualification_manifest.json", "qualification_report.json"]
    elif phase == "export":
        manifest = probe.read(directory / "snapshot_manifest.json")
        output = manifest["snapshot_id"]
        inputs = [
            manifest["descriptor"]["parent_source_dataset_id"],
            manifest["descriptor"]["parent_qualification_id"],
        ]
        reports = ["snapshot_manifest.json", "manifest.sha256"]
    elif phase == "import":
        manifest = probe.read(directory / "snapshot/snapshot_manifest.json")
        output, inputs = manifest["snapshot_id"], [manifest["snapshot_id"]]
        reports = [
            "import_manifest.json",
            "import_manifest.sha256",
            "snapshot/snapshot_manifest.json",
        ]
    elif phase == "curation":
        manifest = probe.read(directory / "curated_manifest.json")
        output = manifest["curated_dataset_id"]
        inputs = [manifest["descriptor"]["parent_snapshot_id"]]
        reports = ["curated_manifest.json", "manifest.sha256"]
    else:
        raise ValueError("preparation_worker_unknown_phase")
    captured: dict[str, Any] = witness.capture(
        phase,
        directory,
        validator_code_sha256=identity["validators_sha256"][phase],
        identity_sha256=digest(identity),
        output_id=output,
        input_ids=inputs,
        report_paths=reports,
    )
    return captured


def run(args: argparse.Namespace) -> None:
    probe = load(ROOT / "scripts/measure_ai09_development_capacity.py", "capacity_worker_probe")
    plan = probe.read(args.output / "preparation-plan.json")
    identity = probe.read(args.output / "preparation-identity.json")
    producer = args.worker in probe.PHASES[:3]
    probe.clean_pin(args.source, identity["producer_commit"])
    probe.clean_pin(ROOT, identity["consumer_commit"])
    actual = runtime(args.source if producer else ROOT, producer=producer)
    key = "producer_runtime_sha256" if producer else "consumer_runtime_sha256"
    if digest(actual) != identity[key] or digest(plan) != identity["plan_sha256"]:
        raise ValueError("preparation_worker_runtime_or_plan_changed")
    # Source never imports the consumer package; the consumer must use this checkout.
    if not producer:
        sys.path.insert(0, str(ROOT / "src"))
    result = probe.observed_worker(args, plan)
    proof = native_witness(args.worker, result, identity)
    if digest(runtime(args.source if producer else ROOT, producer=producer)) != identity[key]:
        raise ValueError("preparation_worker_runtime_changed_during_execution")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result["worker_peak_self_rss_bytes"] = int(peak if sys.platform == "darwin" else peak * 1024)
    probe.write(args.output / (args.worker + ".json"), result)
    probe.write(args.output / (args.worker + ".witness.json"), proof)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--worker", choices=("generation", "qualification", "export", "import", "curation")
    )
    parser.add_argument("--inspect-runtime", choices=("producer", "consumer"))
    args = parser.parse_args()
    if (args.worker is None) == (args.inspect_runtime is None):
        parser.error("select exactly one worker or runtime inspection")
    if args.inspect_runtime:
        probe = load(ROOT / "scripts/measure_ai09_development_capacity.py", "capacity_worker_probe")
        producer = args.inspect_runtime == "producer"
        probe.write(args.output, runtime(args.source if producer else ROOT, producer=producer))
    else:
        probe = load(ROOT / "scripts/measure_ai09_development_capacity.py", "capacity_worker_probe")
        probe.require_remote()
        frozen = probe.read(probe.PLAN_PATH)
        probe.validate_plan(frozen)
        probe.require_dispatch_readiness(frozen)
        if probe.read(args.output / "preparation-plan.json") != frozen:
            raise ValueError("preparation_worker_canonical_plan_mismatch")
        run(args)


if __name__ == "__main__":
    main()
