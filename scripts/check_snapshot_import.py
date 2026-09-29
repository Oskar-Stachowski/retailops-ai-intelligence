"""Fresh-process bounded import/reimport acceptance; no producer checkout or database."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def worker(snapshot: Path, workspace: Path, truth: bool) -> dict[str, object]:
    from retailops_ai.source_snapshot.importer import import_snapshot, verify_import

    before = hashes(snapshot)
    start = time.monotonic()
    first = import_snapshot(snapshot, workspace / "data/generated", allow_evaluation_truth=truth)
    published = hashes(first.directory)
    second = import_snapshot(snapshot, workspace / "data/generated", allow_evaluation_truth=truth)
    verified = verify_import(first.directory, allow_evaluation_truth=truth)
    if (
        first.status != "published"
        or second.status != "reused"
        or hashes(first.directory) != published
        or hashes(snapshot) != before
    ):
        raise ValueError("immutable_import_acceptance_failed")
    elapsed = time.monotonic() - start
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    if elapsed > 300 or peak > 1024:
        raise ValueError("import_smoke_resource_gate_failed")
    return {
        "status": "passed",
        "profile": verified.manifest["source"]["descriptor"]["resolved_parameters"]["profile"],
        "source_dataset_id": verified.source_id,
        "snapshot_id": verified.snapshot_id,
        "source_repository": verified.manifest["source_repository"],
        "source_commit": verified.manifest["exporter"]["git_commit"],
        "tables": len(verified.manifest["tables"]),
        "rows": sum(t["row_count"] for t in verified.manifest["tables"]),
        "snapshot_files": len(before),
        "snapshot_bytes": sum(p.stat().st_size for p in snapshot.rglob("*") if p.is_file()),
        "seconds": elapsed,
        "peak_rss_mib": peak,
        "input_unchanged": True,
        "reimport_unchanged": True,
        "typed_parity": "passed",
        "evaluation_truth": truth,
        "importer": json.loads((first.directory / "import_manifest.json").read_text())["importer"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-dir", type=Path, default=ROOT / "data/fixtures/ai-smoke-v1/snapshot"
    )
    parser.add_argument("--allow-evaluation-truth", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/snapshot-import.json")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workspace", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.workspace is None:
            parser.error("workspace required")
        print(
            json.dumps(
                worker(args.snapshot_dir, args.workspace, args.allow_evaluation_truth),
                sort_keys=True,
            )
        )
        return
    runs = []
    for _ in range(2):
        with tempfile.TemporaryDirectory(prefix="snapshot-import-acceptance-") as temporary:
            command = [
                sys.executable,
                str(Path(__file__).absolute()),
                "--worker",
                "--snapshot-dir",
                str(args.snapshot_dir.absolute()),
                "--workspace",
                str(Path(temporary).resolve()),
            ]
            if args.allow_evaluation_truth:
                command.append("--allow-evaluation-truth")
            # Fixed executable and this script; input paths are individual arguments.
            run = subprocess.run(command, check=True, capture_output=True, text=True, timeout=330)  # noqa: S603
            runs.append(json.loads(run.stdout))
    if len({(r["source_dataset_id"], r["snapshot_id"]) for r in runs}) != 1:
        raise ValueError("repeated_import_identity_mismatch")
    report = {
        "status": "passed",
        "scope": "typed import/reimport/verify, not curated or model readiness",
        "limits": {"seconds_per_run": 300, "rss_mib_per_run": 1024},
        "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": "passed",
                "runs": len(runs),
                "tables": runs[0]["tables"],
                "rows": runs[0]["rows"],
                "report": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
