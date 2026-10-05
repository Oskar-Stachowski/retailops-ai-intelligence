"""Two isolated processes: public snapshots + complete operational capture → immutable full DQ replay."""

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
FIXTURE_SHA = "e899afaeb045a30d62b47fe2f8c9dfb6449ae2fc8f6959750c08e254c7c99775"


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def worker(fixture: Path, lineage_path: Path, workspace: Path) -> dict[str, Any]:
    from retailops_ai.curated.builder import build_curated
    from retailops_ai.full_raw_dq.store import build_replay, verify_replay
    from retailops_ai.source_snapshot.importer import import_snapshot

    if (
        importlib.util.find_spec("data") is not None
        or importlib.util.find_spec("services") is not None
    ):
        raise ValueError("producer_namespace_available_in_isolated_process")
    lineage = json.loads(lineage_path.read_bytes())
    if (
        hashlib.sha256(fixture.read_bytes()).hexdigest() != FIXTURE_SHA
        or lineage["archive_sha256"] != FIXTURE_SHA
    ):
        raise ValueError("full_full_raw_dq_fixture_checksum_mismatch")
    expected_files = {k: v for c in lineage["cases"].values() for k, v in c["files"].items()}
    with ZipFile(fixture) as archive:
        if (
            len(archive.infolist()) != len(expected_files)
            or {i.filename for i in archive.infolist()} != set(expected_files)
            or sum(i.file_size for i in archive.infolist()) > 32 * 1024**2
            or any(
                i.filename.startswith("/") or ".." in Path(i.filename).parts
                for i in archive.infolist()
            )
        ):
            raise ValueError("unbounded_or_unexpected_full_dq_fixture")
        for item in archive.infolist():
            content = archive.read(item)
            if (
                len(content) != expected_files[item.filename]["bytes"]
                or hashlib.sha256(content).hexdigest() != expected_files[item.filename]["sha256"]
            ):
                raise ValueError("full_dq_fixture_file_mismatch")
        archive.extractall(workspace / "inputs")
    before_inputs = hashes(workspace / "inputs")
    started = time.monotonic()
    results = []
    for case in ("demand", "physical"):
        root = workspace / case / "data/generated"
        imported = import_snapshot(
            workspace / "inputs" / case / "public", root, required_use_cases=("anomaly_source",)
        )
        curated = build_curated(imported.directory, root)
        result = build_replay(
            workspace / "inputs" / case / "capture", curated.directory, imported.directory, root
        )
        published = hashes(root)
        repeated = build_replay(
            workspace / "inputs" / case / "capture", curated.directory, imported.directory, root
        )
        verified = verify_replay(Path(result["directory"]), curated.directory, imported.directory)
        if (
            repeated["status"] != "reused"
            or repeated["full_dq_replay_id"] != verified.full_dq_replay_id
            or hashes(root) != published
        ):
            raise ValueError("full_raw_dq_immutability_failed")
        replay = json.loads((Path(result["directory"]) / "replay.json").read_bytes())
        operational = {k: v for k, v in replay.items() if k != "report"}
        canonical = json.dumps(
            operational, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
        reference = lineage["cases"][case]
        expected_report = dict(
            reference["producer_report"], policy_version="ai-full-parent-replay-2.0.0"
        )
        if (
            hashlib.sha256(canonical).hexdigest() != reference["producer_operational_replay_sha256"]
            or replay["report"] != expected_report
            or imported.snapshot.source_id != reference["source_dataset_id"]
            or imported.snapshot.snapshot_id != reference["snapshot_id"]
        ):
            raise ValueError("full_dq_independent_operational_parity_failed")
        results.append(
            {
                "case": case,
                "source_dataset_id": imported.snapshot.source_id,
                "snapshot_id": imported.snapshot.snapshot_id,
                "curated_dataset_id": curated.manifest["curated_dataset_id"],
                "full_dq_replay_id": verified.full_dq_replay_id,
                "replay_sha256": verified.descriptor.replay_sha256,
                "report": result["report"],
                "publication_bytes": sum(
                    p.stat().st_size for p in Path(result["directory"]).rglob("*") if p.is_file()
                ),
            }
        )
    if hashes(workspace / "inputs") != before_inputs:
        raise ValueError("full_raw_dq_source_modified")
    elapsed = time.monotonic() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    if elapsed > 300 or rss > 1024:
        raise ValueError("full_raw_dq_acceptance_resource_limit")
    return {
        "status": "passed",
        "producer_available": False,
        "private_fault_plan_available": False,
        "elapsed_seconds": round(elapsed, 3),
        "peak_rss_mib": round(rss, 3),
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--fixture", type=Path, default=ROOT / "data/fixtures/full-raw-dq-v2.zip")
    parser.add_argument(
        "--lineage", type=Path, default=ROOT / "data/fixtures/full-raw-dq-v2.lineage.json"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() in {
        args.fixture.resolve(),
        args.lineage.resolve(),
    }:
        raise ValueError("receipt_cannot_overwrite_input_fixture")
    if args.worker:
        print(
            json.dumps(
                worker(args.fixture.resolve(), args.lineage.resolve(), args.worker.resolve()),
                sort_keys=True,
            )
        )
        return 0
    runs = []
    for _ in range(2):
        with tempfile.TemporaryDirectory(prefix="ai07-dq-acceptance-") as tmp:
            result = subprocess.run(  # noqa: S603 - fixed interpreter/script, no shell
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).resolve()),
                    "--worker",
                    tmp,
                    "--fixture",
                    str(args.fixture.resolve()),
                    "--lineage",
                    str(args.lineage.resolve()),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=360,
            )
            if result.returncode:
                raise RuntimeError("raw DQ acceptance worker failed: " + result.stderr[-8192:])
            runs.append(json.loads(result.stdout))
    if runs[0]["cases"] != runs[1]["cases"]:
        raise ValueError("full_raw_dq_not_reproducible")
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
