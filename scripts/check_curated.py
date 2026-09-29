"""Fresh-process import/curated/rebuild/as-of acceptance without producer or database."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import subprocess
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).absolute().parents[1]


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def worker(snapshot: Path, workspace: Path, truth: bool) -> dict[str, Any]:
    from retailops_ai.curated.builder import build_curated, iter_rows, verify_curated
    from retailops_ai.curated.reader import rows_as_of
    from retailops_ai.source_snapshot.importer import import_snapshot

    before = hashes(snapshot)
    start = time.monotonic()
    imported = import_snapshot(snapshot, workspace / "data/generated", allow_evaluation_truth=truth)
    source_bytes = hashes(imported.directory)
    reimported = import_snapshot(
        snapshot, workspace / "data/generated", allow_evaluation_truth=truth
    )
    if reimported.status != "reused" or hashes(imported.directory) != source_bytes:
        raise ValueError("reimport_changed_immutable_input")
    first = build_curated(
        imported.directory, workspace / "data/generated", allow_evaluation_truth=truth
    )
    published = hashes(first.directory)
    second = build_curated(
        imported.directory, workspace / "data/generated", allow_evaluation_truth=truth
    )
    verified = verify_curated(first.directory)
    if (
        first.status != "published"
        or second.status != "reused"
        or hashes(first.directory) != published
        or hashes(imported.directory) != source_bytes
        or hashes(snapshot) != before
        or any("truth" in p for p in published)
    ):
        raise ValueError("curated_immutability_or_truth_gate_failed")
    params = verified["descriptor"]["source_parameters"]
    start_date = date.fromisoformat(params["start_date"])
    origins = [
        datetime.combine(
            start_date + timedelta(days=max(7, params["warmup_days"])), datetime.min.time(), UTC
        )
        - timedelta(seconds=1),
        datetime.combine(
            date.fromisoformat(params["end_date"]) + timedelta(days=1), datetime.min.time(), UTC
        )
        - timedelta(seconds=1),
    ]
    source_spec = next(
        t for t in imported.snapshot.manifest["tables"] if t["table"] == "daily_demand_versions"
    )
    correction = next(
        (row for row in iter_rows(snapshot, source_spec["files"], 8192) if row["version"] > 1),
        None,
    )
    if correction is not None:
        cutoff = correction["available_at"]
        origins.extend([cutoff - timedelta(microseconds=1), cutoff])
    comparisons = []
    for origin in origins:
        expected: dict[tuple[Any, ...], dict[str, Any]] = {}
        versions = 0
        for row in iter_rows(snapshot, source_spec["files"], 8192):
            versions += 1
            if row["available_at"] > origin or row["business_date"] > origin.date():
                continue
            key = tuple(
                row[k] for k in ("business_date", "product_id", "selling_location_id", "channel")
            )
            if key not in expected or row["version"] > expected[key]["version"]:
                expected[key] = row
        observed = {
            tuple(
                row[k] for k in ("business_date", "product_id", "selling_location_id", "channel")
            ): row
            for row in rows_as_of(first.directory, origin)
        }
        if set(observed) != set(expected) or any(
            observed[key][field] != value[field]
            for key, value in expected.items()
            for field in ("version", "observed_units", "observation_status")
        ):
            raise ValueError("independent_source_as_of_comparison_failed")
        comparisons.append(
            {
                "origin": origin.isoformat(),
                "selected_observations": len(observed),
                "source_versions": versions,
                "status": "passed",
            }
        )
        if correction is not None and origin in origins[-2:]:
            key = tuple(
                correction[k]
                for k in ("business_date", "product_id", "selling_location_id", "channel")
            )
            chosen = observed.get(key)
            if (
                chosen is None
                or (
                    origin < correction["available_at"]
                    and chosen["version"] >= correction["version"]
                )
                or (
                    origin == correction["available_at"]
                    and chosen["version"] != correction["version"]
                )
            ):
                raise ValueError("real_late_correction_boundary_failed")
    elapsed = time.monotonic() - start
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    if elapsed > 300 or peak > 1024:
        raise ValueError("curated_resource_gate_failed")
    return {
        "status": "passed",
        "profile": params["profile"],
        "source_dataset_id": imported.snapshot.source_id,
        "snapshot_id": imported.snapshot.snapshot_id,
        "curated_dataset_id": verified["curated_dataset_id"],
        "producer_commit": imported.snapshot.manifest["exporter"]["git_commit"],
        "tables": len(verified["tables"]),
        "rows": sum(t["row_count"] for t in verified["tables"]),
        "quarantine_rows": verified["quarantine"]["row_count"],
        "seconds": elapsed,
        "peak_rss_mib": peak,
        "curated_files": len(published),
        "curated_bytes": sum(p.stat().st_size for p in first.directory.rglob("*") if p.is_file()),
        "input_unchanged": True,
        "rebuild_unchanged": True,
        "reimport_unchanged": True,
        "evaluation_truth_in_curated": False,
        "as_of_checks": comparisons,
        "late_correction": {
            "status": "passed" if correction is not None else "not_present",
            "observation_id": correction["observation_id"] if correction else None,
            "version": correction["version"] if correction else None,
            "available_at": correction["available_at"].isoformat() if correction else None,
        },
        "logical_tables": [
            {
                k: t[k]
                for k in (
                    "table",
                    "row_count",
                    "content_sha256",
                    "grain",
                    "schema",
                    "field_ranges",
                )
            }
            for t in verified["tables"]
        ],
        "transform": verified["descriptor"]["transform"],
        "readiness": verified["readiness"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-dir", type=Path, default=ROOT / "data/fixtures/ai-smoke-v1/snapshot"
    )
    parser.add_argument("--allow-evaluation-truth", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/curated.json")
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
        with tempfile.TemporaryDirectory(prefix="curated-acceptance-") as temporary:
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
            # Fixed executable/script; data paths are separate arguments.
            run = subprocess.run(command, check=True, capture_output=True, text=True, timeout=330)  # noqa: S603
            runs.append(json.loads(run.stdout))
    if len({r["curated_dataset_id"] for r in runs}) != 1:
        raise ValueError("repeated_curated_identity_mismatch")
    report = {
        "status": "passed",
        "scope": "import/build/rebuild/verify/as-of; generation and cross-repo 03.6 excluded",
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
