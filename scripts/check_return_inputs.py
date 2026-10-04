"""Two independent public-snapshot processes prove bounded immutable return-day views."""

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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "c344a5cfeea086e1054add724c830d747815e9eab425a19a17c1d0691671066c"


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def worker(source: Path, workspace: Path) -> dict[str, Any]:
    from retailops_ai.curated.builder import build_curated
    from retailops_ai.curated.reader import CuratedReader
    from retailops_ai.return_inputs.contract import Policy
    from retailops_ai.return_inputs.store import build_inputs, parent_inputs, verify_inputs
    from retailops_ai.source_snapshot.importer import import_snapshot

    if (
        importlib.util.find_spec("data") is not None
        or importlib.util.find_spec("services") is not None
    ):
        raise ValueError("producer_namespace_available_in_return_acceptance")
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError("return_source_fixture_checksum_mismatch")
    with ZipFile(source) as archive:
        if sum(r.file_size for r in archive.infolist()) > 32 * 1024**2 or any(
            r.filename.startswith("/") or ".." in Path(r.filename).parts for r in archive.infolist()
        ):
            raise ValueError("unbounded_return_source_fixture")
        archive.extractall(workspace / "source")
    before = hashes(workspace / "source")
    started = time.monotonic()
    cases = []
    for case in ("demand", "physical"):
        root = workspace / case / "data/generated"
        imported = import_snapshot(
            workspace / "source" / case / "public", root, required_use_cases=("anomaly_source",)
        )
        curated = build_curated(imported.directory, root)
        reader = CuratedReader(curated.directory)
        _, tables = parent_inputs(curated.directory)
        history_members = list(reader.rows(datetime(2026, 8, 1, tzinfo=UTC), table="return_events"))
        results = []
        for name, end, origin in (
            ("history", date(2026, 7, 31), datetime(2026, 8, 1, tzinfo=UTC)),
            ("history_corrected", date(2026, 7, 31), datetime(2026, 9, 9, tzinfo=UTC)),
            ("full_tail", date(2026, 9, 8), datetime(2026, 9, 9, tzinfo=UTC)),
        ):
            policy = Policy(start_date=date(2026, 7, 2), end_date=end, as_of_time=origin)
            result = build_inputs(curated.directory, root, policy)
            published = hashes(root)
            repeated = build_inputs(curated.directory, root, policy)
            verified = verify_inputs(result.directory, curated.directory)
            points = [
                json.loads(line)
                for line in (result.directory / "returns.jsonl").read_bytes().splitlines()
            ]
            expected = [
                r
                for r in tables["return_events"]
                if r["curated_available_at"] is not None
                and r["curated_available_at"] <= origin
                and policy.start_date <= r["returned_at"].date() <= end
            ]
            native = list(reader.rows(origin, table="return_events"))
            selected = [r for r in native if policy.start_date <= r["returned_at"].date() <= end]
            if (
                repeated.status != "reused"
                or repeated.manifest != verified
                or hashes(root) != published
                or sum(p["known_event_count"] for p in points) != len(expected)
                or {r["id"] for r in expected} != {r["id"] for r in selected}
                or any(
                    p["observed_return_units"] is not None or p["status"] != "insufficient_data"
                    for p in points
                )
            ):
                raise ValueError("return_view_replay_or_readiness_failed")
            results.append(
                {
                    "view": name,
                    "return_input_id": verified.return_input_id,
                    "content_sha256": verified.descriptor.content_sha256,
                    "rows": len(points),
                    "known_events": sum(p["known_event_count"] for p in points),
                    "known_refunded_units": sum(p["known_refunded_units"] for p in points),
                    "known_rejected_units": sum(p["known_rejected_units"] for p in points),
                    "post_sales_history_events": sum(
                        p["known_event_count"] for p in points if p["business_date"] > "2026-07-31"
                    ),
                    "publication_bytes": sum(
                        p.stat().st_size for p in result.directory.rglob("*") if p.is_file()
                    ),
                }
            )
        # Re-querying the earlier origin after tail processing must preserve its membership.
        if (
            list(reader.rows(datetime(2026, 8, 1, tzinfo=UTC), table="return_events"))
            != history_members
        ):
            raise ValueError("return_historical_membership_changed")
        cases.append(
            {
                "case": case,
                "source_dataset_id": imported.snapshot.source_id,
                "snapshot_id": imported.snapshot.snapshot_id,
                "curated_dataset_id": curated.manifest["curated_dataset_id"],
                "views": results,
            }
        )
    if hashes(workspace / "source") != before:
        raise ValueError("return_source_modified")
    elapsed = time.monotonic() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
        1024**2 if sys.platform == "darwin" else 1024
    )
    if elapsed > 300 or rss > 1024:
        raise ValueError(
            f"return_acceptance_resource_limit: seconds={elapsed:.3f}, rss_mib={rss:.3f}"
        )
    return {
        "status": "passed",
        "producer_available": False,
        "elapsed_seconds": round(elapsed, 3),
        "peak_rss_mib": round(rss, 3),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument(
        "--source-fixture", type=Path, default=ROOT / "data/fixtures/anomaly-v1_2.zip"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.source_fixture.resolve():
        raise ValueError("receipt_cannot_overwrite_return_source")
    if args.worker:
        print(
            json.dumps(worker(args.source_fixture.resolve(), args.worker.resolve()), sort_keys=True)
        )
        return 0
    runs = []
    for _ in range(2):
        with tempfile.TemporaryDirectory(prefix="ai07-return-acceptance-") as temporary:
            result = subprocess.run(  # noqa: S603 - fixed interpreter/script, no shell
                [
                    sys.executable,
                    "-I",
                    str(Path(__file__).resolve()),
                    "--worker",
                    temporary,
                    "--source-fixture",
                    str(args.source_fixture.resolve()),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=360,
            )
            if result.returncode:
                raise RuntimeError("return acceptance worker failed: " + result.stderr[-8192:])
            runs.append(json.loads(result.stdout))
    if runs[0]["cases"] != runs[1]["cases"]:
        raise ValueError("return_inputs_not_reproducible")
    report = {
        "status": "passed",
        "source_fixture_sha256": SOURCE_SHA,
        "limits": {"seconds_per_process": 300, "peak_rss_mib": 1024},
        "runs": runs,
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
