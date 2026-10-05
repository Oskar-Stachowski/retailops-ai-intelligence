"""Two fresh processes: frozen public handoff → curated → inputs → full replay."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SHA = "c344a5cfeea086e1054add724c830d747815e9eab425a19a17c1d0691671066c"


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def worker(archive_path: Path, workspace: Path) -> dict[str, Any]:
    from retailops_ai.anomalies.store import build_inputs, verify_inputs
    from retailops_ai.curated.builder import build_curated
    from retailops_ai.source_snapshot.importer import import_snapshot

    if importlib.util.find_spec("data") is not None:
        raise ValueError("producer_namespace_available_in_isolated_process")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != FIXTURE_SHA:
        raise ValueError("frozen_anomaly_fixture_checksum_mismatch")
    with ZipFile(archive_path) as archive:
        if sum(r.file_size for r in archive.infolist()) > 32 * 1024**2 or any(
            r.filename.startswith("/") or ".." in Path(r.filename).parts for r in archive.infolist()
        ):
            raise ValueError("unbounded_anomaly_fixture")
        archive.extractall(workspace / "fixture")
    before = hashes(workspace / "fixture")
    started = time.monotonic()
    results = []
    for case in ("demand", "physical"):
        root = workspace / case / "data/generated"
        imported = import_snapshot(
            workspace / "fixture" / case / "public", root, required_use_cases=("anomaly_source",)
        )
        curated = build_curated(imported.directory, root)
        inputs = build_inputs(curated.directory, root)
        published = hashes(root)
        repeated = build_inputs(curated.directory, root)
        verified = verify_inputs(inputs.directory, curated.directory)
        if repeated.status != "reused" or verified != inputs.manifest or hashes(root) != published:
            raise ValueError("anomaly_input_immutability_failed")
        if any("truth" in path for path in hashes(inputs.directory)):
            raise ValueError("truth_in_normal_anomaly_inputs")
        results.append(
            {
                "case": case,
                "source_dataset_id": imported.snapshot.source_id,
                "snapshot_id": imported.snapshot.snapshot_id,
                "curated_dataset_id": curated.manifest["curated_dataset_id"],
                "anomaly_input_id": verified.anomaly_input_id,
                "content_sha256": verified.descriptor.content_sha256,
                "row_count": verified.descriptor.row_count,
                "status_counts": verified.descriptor.status_counts,
                "file_bytes": verified.file_bytes,
            }
        )
    if hashes(workspace / "fixture") != before:
        raise ValueError("anomaly_snapshot_modified")
    elapsed = time.monotonic() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    if elapsed > 300 or rss > 1024:
        raise ValueError("anomaly_acceptance_resource_limit")
    return {
        "status": "passed",
        "producer_available": False,
        "elapsed_seconds": round(elapsed, 3),
        "peak_rss_mib": round(rss, 3),
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--fixture", type=Path, default=ROOT / "data/fixtures/anomaly-v1_2.zip")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.fixture.resolve():
        raise ValueError("receipt_cannot_overwrite_input_fixture")
    if args.worker:
        print(json.dumps(worker(args.fixture.resolve(), args.worker.resolve()), sort_keys=True))
        return 0
    runs = []
    for _ in range(2):
        with tempfile.TemporaryDirectory(prefix="ai07-input-acceptance-") as temporary:
            result = subprocess.run(  # noqa: S603 - fixed interpreter/script, no shell
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).resolve()),
                    "--worker",
                    temporary,
                    "--fixture",
                    str(args.fixture.resolve()),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=360,
            )
            if result.returncode:
                raise RuntimeError("anomaly acceptance worker failed: " + result.stderr[-8192:])
            runs.append(json.loads(result.stdout))
    if runs[0]["cases"] != runs[1]["cases"]:
        raise ValueError("anomaly_inputs_not_reproducible")
    report = {
        "status": "passed",
        "fixture_sha256": FIXTURE_SHA,
        "limits": {"seconds_per_process": 300, "peak_rss_mib": 1024},
        "runs": runs,
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
