"""Run one balanced file shard from the complete native pytest collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
TIMINGS = ROOT / "scripts/ci_test_timings.json"


def assign_files(files: set[str], weights: dict[str, float], count: int) -> list[list[str]]:
    """Assign every current file exactly once; new files receive a positive default."""
    if count < 1 or count > len(files):
        raise ValueError("shard count must be between one and the current file count")
    if any(not math.isfinite(value) or value <= 0 for value in weights.values()):
        raise ValueError("file timing weights must be positive")
    groups: list[list[str]] = [[] for _ in range(count)]
    totals = [0.0] * count
    for name in sorted(files, key=lambda name: (-weights.get(name, 1.0), name)):
        index = min(range(count), key=lambda index: (totals[index], index))
        groups[index].append(name)
        totals[index] += weights.get(name, 1.0)
    return [sorted(group) for group in groups]


class Shard:
    def __init__(
        self, index: int, count: int, report: Path | None, timings: Path | None = None
    ) -> None:
        self.index = index
        self.count = count
        self.report = report
        self.timings = timings
        self.full_ids: list[str] = []
        self.selected_ids: list[str] = []
        self.phases: dict[str, dict[str, dict[str, Any]]] = {}

    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        ids = [item.nodeid for item in items]
        if len(ids) != len(set(ids)):
            raise pytest.UsageError("duplicate node IDs in full collection")
        files = {item.path.relative_to(ROOT).as_posix() for item in items}
        weights = json.loads(TIMINGS.read_text())["seconds"]
        groups = assign_files(files, weights, self.count)
        paths = set(groups[self.index])
        selected = [item for item in items if item.path.relative_to(ROOT).as_posix() in paths]
        deselected = [item for item in items if item.path.relative_to(ROOT).as_posix() not in paths]
        if not selected or len(selected) + len(deselected) != len(items):
            raise pytest.UsageError("empty or incomplete CI shard")
        self.full_ids = sorted(ids)
        self.selected_ids = sorted(item.nodeid for item in selected)
        plan = {
            "version": "ci-complete-test-shards-1.0.0",
            "full_collection_count": len(items),
            "full_collection_sha256": hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest(),
            "shard": self.index,
            "shards": self.count,
            "selected_count": len(selected),
            "groups": [
                {
                    "files": group,
                    "node_ids": sorted(
                        item.nodeid
                        for item in items
                        if item.path.relative_to(ROOT).as_posix() in set(group)
                    ),
                    "estimated_seconds": sum(weights.get(name, 1.0) for name in group),
                }
                for group in groups
            ],
        }
        if self.report is not None:
            self.report.parent.mkdir(parents=True, exist_ok=True)
            self.report.write_text(json.dumps(plan, indent=2) + "\n")
        print(
            json.dumps(
                {key: value for key, value in plan.items() if key != "groups"},
                sort_keys=True,
            ),
            flush=True,
        )
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.phases.setdefault(report.nodeid, {})[report.when] = {
            "seconds": report.duration,
            "outcome": report.outcome,
        }

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if self.timings is None:
            return
        seconds: dict[str, float] = {}
        for node, phases in self.phases.items():
            name = node.split("::", 1)[0]
            seconds[name] = seconds.get(name, 0.0) + sum(p["seconds"] for p in phases.values())
        result = {
            "version": "ci-test-execution-1.0.0",
            "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
            "commit": os.environ.get("GITHUB_SHA", "local"),
            "shard": self.index,
            "shards": self.count,
            "exit_code": int(exitstatus),
            "full_collection_sha256": hashlib.sha256("\n".join(self.full_ids).encode()).hexdigest(),
            "full_node_ids": self.full_ids,
            "selected_node_ids": self.selected_ids,
            "phases": self.phases,
            "seconds": seconds,
        }
        self.timings.parent.mkdir(parents=True, exist_ok=True)
        self.timings.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.shards < 1 or not 0 <= args.shard < args.shards:
        parser.error("shard index must be within the positive shard count")
    output = ROOT / "reports" / f"ci-tests-{args.shard}"
    output.parent.mkdir(parents=True, exist_ok=True)
    report = args.report or output.with_suffix(".plan.json")
    timings = None if args.plan_only else output.with_suffix(".timings.json")
    options = [str(ROOT / "tests")]
    if args.plan_only:
        options.extend(["--collect-only", "-q"])
    else:
        options.extend(["--durations=30", "--junitxml=" + str(output.with_suffix(".xml"))])
    return int(pytest.main(options, plugins=[Shard(args.shard, args.shards, report, timings)]))


if __name__ == "__main__":
    raise SystemExit(main())
