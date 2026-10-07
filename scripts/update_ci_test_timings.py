"""Validate complete shard evidence before producing weights for a reviewed update."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def validated_timings(reports: list[dict[str, Any]]) -> dict[str, Any]:
    # Retain failed attempt artifacts. A retry may reuse successful shards from
    # the same run and commit, but can never fall back behind a newer failure.
    newest: dict[int, tuple[int, dict[str, Any]]] = {}
    attempts: set[tuple[int, int]] = set()
    identities = {(r["run_id"], r["commit"]) for r in reports}
    if len(identities) != 1:
        raise ValueError("mixed_run_or_commit")
    for report in reports:
        attempt_text = str(report["run_attempt"])
        if attempt_text != "local" and (not attempt_text.isdigit() or int(attempt_text) < 1):
            raise ValueError("invalid_run_attempt")
        attempt = 0 if attempt_text == "local" else int(attempt_text)
        shard = report["shard"]
        if type(shard) is not int or shard not in range(4) or (shard, attempt) in attempts:
            raise ValueError("invalid_or_duplicate_shard_attempt")
        attempts.add((shard, attempt))
        if shard not in newest or attempt > newest[shard][0]:
            newest[shard] = (attempt, report)
    reports = [value[1] for _, value in sorted(newest.items())]
    if len(reports) != 4 or {r["shard"] for r in reports} != set(range(4)):
        raise ValueError("exactly_four_distinct_shards_required")
    first = reports[0]
    full = first["full_node_ids"]
    if not full or full != sorted(set(full)):
        raise ValueError("invalid_full_collection")
    digest = hashlib.sha256("\n".join(full).encode()).hexdigest()
    seen: set[str] = set()
    seconds: dict[str, float] = {}
    for report in reports:
        if (
            report["version"] != "ci-test-execution-1.0.0"
            or report["shards"] != 4
            or report["exit_code"] != 0
            or report["full_collection_sha256"] != digest
            or report["full_node_ids"] != full
            or any(report[key] != first[key] for key in ("run_id", "commit"))
        ):
            raise ValueError("inconsistent_or_failed_shard")
        selected = report["selected_node_ids"]
        if not selected or selected != sorted(set(selected)) or seen.intersection(selected):
            raise ValueError("empty_or_overlapping_shard")
        if set(report["phases"]) != set(selected):
            raise ValueError("selected_tests_not_executed")
        measured: dict[str, float] = {}
        for node, phases in report["phases"].items():
            if not {"setup", "teardown"} <= set(phases) <= {"setup", "call", "teardown"}:
                raise ValueError("incomplete_test_phases")
            if "call" not in phases and phases["setup"]["outcome"] != "skipped":
                raise ValueError("missing_test_call")
            name = node.split("::", 1)[0]
            for phase in phases.values():
                duration = phase["seconds"]
                if (
                    phase["outcome"] not in {"passed", "skipped"}
                    or type(duration) not in {float, int}
                    or not math.isfinite(duration)
                    or duration < 0
                ):
                    raise ValueError("failed_or_invalid_test_phase")
                measured[name] = measured.get(name, 0.0) + duration
        if set(measured) != set(report["seconds"]) or any(
            not math.isclose(value, report["seconds"][name], abs_tol=1e-6)
            for name, value in measured.items()
        ):
            raise ValueError("file_timings_do_not_match_executed_phases")
        if seconds.keys() & measured.keys():
            raise ValueError("test_file_split_between_shards")
        seconds.update(measured)
        seen.update(selected)
    if seen != set(full):
        raise ValueError("incomplete_test_collection")
    return {
        "version": "ci-test-file-timings-1.1.0",
        "measured_run": first["run_id"],
        "measured_commit": first["commit"],
        "measured_attempts": {str(r["shard"]): r["run_attempt"] for r in reports},
        "note": "Measured setup/call/teardown seconds from four complete passing Linux shards.",
        "seconds": {name: max(0.1, round(value, 3)) for name, value in sorted(seconds.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--check", action="store_true")
    choice.add_argument("--output", type=Path)
    args = parser.parse_args()
    reports = [
        json.loads(path.read_text()) for path in sorted(args.reports.rglob("*.timings.json"))
    ]
    result = validated_timings(reports)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"Complete disjoint test execution verified: {len(result['seconds'])} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
