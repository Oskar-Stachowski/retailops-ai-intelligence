"""Run one balanced file shard from the complete native pytest collection."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TIMINGS = ROOT / "scripts/ci_test_timings.json"


def assign_files(files: set[str], weights: dict[str, float], count: int) -> list[list[str]]:
    """Assign every current file exactly once; new files receive a positive default."""
    if count < 1 or count > len(files):
        raise ValueError("shard count must be between one and the current file count")
    if any(value <= 0 for value in weights.values()):
        raise ValueError("file timing weights must be positive")
    groups: list[list[str]] = [[] for _ in range(count)]
    totals = [0.0] * count
    for name in sorted(files, key=lambda name: (-weights.get(name, 1.0), name)):
        index = min(range(count), key=lambda index: (totals[index], index))
        groups[index].append(name)
        totals[index] += weights.get(name, 1.0)
    return [sorted(group) for group in groups]


class Shard:
    def __init__(self, index: int, count: int, report: Path | None) -> None:
        self.index = index
        self.count = count
        self.report = report

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--shards", type=int, required=True)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.shards < 1 or not 0 <= args.shard < args.shards:
        parser.error("shard index must be within the positive shard count")
    options = [str(ROOT / "tests")]
    if args.plan_only:
        options.extend(["--collect-only", "-q"])
    return int(pytest.main(options, plugins=[Shard(args.shard, args.shards, args.report)]))


if __name__ == "__main__":
    raise SystemExit(main())
