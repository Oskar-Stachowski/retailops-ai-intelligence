"""Two isolated processes: public snapshots + operational capture → immutable DQ replay."""

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
SOURCE_SHA = "c344a5cfeea086e1054add724c830d747815e9eab425a19a17c1d0691671066c"
RAW_SHA = "4640ed308864d872715cc32f6a055e3e18238fe5bd268e97734860d249472e06"


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def worker(source: Path, raw: Path, workspace: Path) -> dict[str, Any]:
    from retailops_ai.curated.builder import build_curated
    from retailops_ai.raw_dq.store import build_replay, verify_replay
    from retailops_ai.source_snapshot.importer import import_snapshot

    if (
        importlib.util.find_spec("data") is not None
        or importlib.util.find_spec("services") is not None
    ):
        raise ValueError("producer_namespace_available_in_isolated_process")
    for archive_path, expected, target in ((source, SOURCE_SHA, "source"), (raw, RAW_SHA, "raw")):
        if hashlib.sha256(archive_path.read_bytes()).hexdigest() != expected:
            raise ValueError("raw_dq_fixture_checksum_mismatch")
        with ZipFile(archive_path) as archive:
            if sum(r.file_size for r in archive.infolist()) > 32 * 1024**2 or any(
                r.filename.startswith("/") or ".." in Path(r.filename).parts
                for r in archive.infolist()
            ):
                raise ValueError("unbounded_raw_dq_fixture")
            archive.extractall(workspace / target)
    before_source, before_raw = hashes(workspace / "source"), hashes(workspace / "raw")
    started = time.monotonic()
    results = []
    for case in ("demand", "physical"):
        root = workspace / case / "data/generated"
        imported = import_snapshot(
            workspace / "source" / case / "public", root, required_use_cases=("anomaly_source",)
        )
        curated = build_curated(imported.directory, root)
        result = build_replay(workspace / "raw" / case, curated.directory, root)
        published = hashes(root)
        repeated = build_replay(workspace / "raw" / case, curated.directory, root)
        verified = verify_replay(Path(result["directory"]), curated.directory)
        if (
            repeated["status"] != "reused"
            or repeated["dq_replay_id"] != verified.dq_replay_id
            or hashes(root) != published
        ):
            raise ValueError("raw_dq_immutability_failed")
        results.append(
            {
                "case": case,
                "source_dataset_id": imported.snapshot.source_id,
                "snapshot_id": imported.snapshot.snapshot_id,
                "curated_dataset_id": curated.manifest["curated_dataset_id"],
                "dq_replay_id": verified.dq_replay_id,
                "replay_sha256": verified.descriptor.replay_sha256,
                "report": result["report"],
                "publication_bytes": sum(
                    p.stat().st_size for p in Path(result["directory"]).rglob("*") if p.is_file()
                ),
            }
        )
    if hashes(workspace / "source") != before_source or hashes(workspace / "raw") != before_raw:
        raise ValueError("raw_dq_source_modified")
    elapsed = time.monotonic() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    if elapsed > 300 or rss > 1024:
        raise ValueError("raw_dq_acceptance_resource_limit")
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
    parser.add_argument(
        "--source-fixture", type=Path, default=ROOT / "data/fixtures/anomaly-v1_2.zip"
    )
    parser.add_argument("--raw-fixture", type=Path, default=ROOT / "data/fixtures/raw-dq-v1.zip")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() in {
        args.source_fixture.resolve(),
        args.raw_fixture.resolve(),
    }:
        raise ValueError("receipt_cannot_overwrite_input_fixture")
    if args.worker:
        print(
            json.dumps(
                worker(
                    args.source_fixture.resolve(), args.raw_fixture.resolve(), args.worker.resolve()
                ),
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
                    "--source-fixture",
                    str(args.source_fixture.resolve()),
                    "--raw-fixture",
                    str(args.raw_fixture.resolve()),
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
        raise ValueError("raw_dq_not_reproducible")
    report = {
        "status": "passed",
        "source_fixture_sha256": SOURCE_SHA,
        "raw_fixture_sha256": RAW_SHA,
        "limits": {"seconds_per_process": 300, "peak_rss_mib": 1024},
        "runs": runs,
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
