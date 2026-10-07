"""Emit an offline native-fixture replay receipt, never a live source qualification."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from retailops_ai.source_replay.drill import run_drill

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ai10-observation-replay-") as directory:
        report = run_drill(ROOT / "data/fixtures/ai-smoke-v1/snapshot", Path(directory).resolve())
    raw = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        # Exclusive creation keeps previous evidence immutable.
        args.report.parent.mkdir(parents=True, exist_ok=True)
        try:
            with args.report.open("x", encoding="utf-8") as output:
                output.write(raw)
        except FileExistsError:
            if args.report.is_symlink() or args.report.stat().st_size != len(raw.encode()):
                raise ValueError("observation_replay_existing_report_differs") from None
            if args.report.read_text(encoding="utf-8") != raw:
                raise ValueError("observation_replay_existing_report_differs") from None
    print(raw, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
