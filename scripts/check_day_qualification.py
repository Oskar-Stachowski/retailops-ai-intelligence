"""Two isolated consumer processes: public parents and closures → causal DQ-qualified days."""

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


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def extract(fixture_dir: Path, name: str, destination: Path) -> dict[str, Any]:
    lineage: dict[str, Any] = json.loads((fixture_dir / (name + ".lineage.json")).read_bytes())
    archive_path = fixture_dir / (name + ".zip")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != lineage["archive_sha256"]:
        raise ValueError("day_qualification_fixture_checksum_mismatch")
    expected = {k: v for c in lineage["cases"].values() for k, v in c["files"].items()}
    with ZipFile(archive_path) as archive:
        if (
            len(archive.infolist()) != len(expected)
            or {i.filename for i in archive.infolist()} != set(expected)
            or sum(i.file_size for i in archive.infolist()) > 32 * 1024**2
            or any(
                i.filename.startswith("/") or ".." in Path(i.filename).parts
                for i in archive.infolist()
            )
        ):
            raise ValueError("day_qualification_unbounded_fixture")
        for item in archive.infolist():
            raw = archive.read(item)
            spec = expected[item.filename]
            if len(raw) != spec["bytes"] or hashlib.sha256(raw).hexdigest() != spec["sha256"]:
                raise ValueError("day_qualification_fixture_member_mismatch")
        archive.extractall(destination)
    return lineage


def worker(fixture_dir: Path, workspace: Path) -> dict[str, Any]:
    from retailops_ai.curated.builder import build_curated
    from retailops_ai.day_qualification.store import build, verify
    from retailops_ai.full_raw_dq.store import build_replay
    from retailops_ai.source_snapshot.importer import import_snapshot

    if (
        importlib.util.find_spec("data") is not None
        or importlib.util.find_spec("services") is not None
    ):
        raise ValueError("producer_namespace_available_in_isolated_process")
    extract(fixture_dir, "full-raw-dq-v2", workspace / "inputs/full")
    lineage = extract(fixture_dir, "day-coverage-v1", workspace / "inputs/coverage")
    before = hashes(workspace / "inputs")
    started = time.monotonic()
    results = []
    for case in ("demand", "physical"):
        root = workspace / case / "data/generated"
        source = import_snapshot(
            workspace / "inputs/full" / case / "public",
            root,
            required_use_cases=("anomaly_source",),
        )
        curated = build_curated(source.directory, root)
        replay = build_replay(
            workspace / "inputs/full" / case / "capture", curated.directory, source.directory, root
        )
        coverage = workspace / "inputs/coverage" / case
        args = (Path(replay["directory"]), coverage, curated.directory, source.directory)
        result = build(*args, root)
        generated_before_verify = hashes(root)
        manifest = verify(Path(result["directory"]), *args)
        if (
            hashes(root) != generated_before_verify
            or result["day_qualification_id"] != manifest.day_qualification_id
            or manifest.descriptor.coverage.coverage_id != lineage["cases"][case]["coverage_id"]
            or not result["status_counts"].get("qualified")
            or not result["status_counts"].get("dq_missing_facts")
        ):
            raise ValueError("day_qualification_acceptance_mismatch")
        results.append(
            {
                "case": case,
                "source_dataset_id": source.snapshot.source_id,
                "coverage_id": manifest.descriptor.coverage.coverage_id,
                "full_dq_replay_id": manifest.descriptor.full_dq_replay_id,
                "day_qualification_id": manifest.day_qualification_id,
                "rows_sha256": manifest.descriptor.rows_sha256,
                "row_count": manifest.descriptor.row_count,
                "status_counts": manifest.descriptor.status_counts,
                "artifact_files": hashes(Path(result["directory"])),
            }
        )
    elapsed = time.monotonic() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    if hashes(workspace / "inputs") != before or elapsed > 300 or rss > 1024:
        raise ValueError("day_qualification_mutation_or_process_budget")
    return {
        "elapsed_seconds": round(elapsed, 3),
        "peak_rss_mib": round(rss, 3),
        "producer_imports_available": False,
        "evaluation_truth_available": False,
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--fixture-dir", type=Path, default=ROOT / "data/fixtures")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve().is_relative_to(args.fixture_dir.resolve()):
        raise ValueError("receipt_cannot_overwrite_fixture_directory")
    if args.worker:
        report = worker(args.fixture_dir.resolve(), args.worker.resolve())
    else:
        runs = []
        for _ in range(2):
            with tempfile.TemporaryDirectory(prefix="ai07-day-qualification-") as tmp:
                run = subprocess.run(  # noqa: S603 - fixed interpreter/script
                    [
                        sys.executable,
                        "-I",
                        str(Path(__file__).resolve()),
                        "--worker",
                        tmp,
                        "--fixture-dir",
                        str(args.fixture_dir.resolve()),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=360,
                )  # noqa: S603 - fixed interpreter/script
                if run.returncode:
                    raise RuntimeError("day qualification worker failed: " + run.stderr[-8192:])
                runs.append(json.loads(run.stdout))
        if runs[0]["cases"] != runs[1]["cases"]:
            raise ValueError("day_qualification_not_reproducible")
        report = {
            "status": "passed",
            "limits": {"seconds_per_process": 300, "peak_rss_mib": 1024},
            "runs": runs,
        }
        if args.output:
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
