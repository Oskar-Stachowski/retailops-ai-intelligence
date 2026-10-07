"""Select unchanged native lanes conservatively; never reuse a scientific qualification."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

ANOMALY_ONLY = {"scripts/check_anomaly_oci.py", "scripts/accept_anomaly_lifecycle.py"}
STOCKOUT_ONLY = {"scripts/check_stockout_final_acceptance.py"}


def lanes(paths: set[str], manual: str | None = None) -> tuple[bool, bool]:
    if manual is not None:
        if manual not in {"all", "stockout", "anomaly"}:
            raise ValueError("ai10_native_lane_invalid")
        return manual != "anomaly", manual != "stockout"
    if not paths or paths - ANOMALY_ONLY - STOCKOUT_ONLY:
        return True, True
    return bool(paths & STOCKOUT_ONLY), bool(paths & ANOMALY_ONLY)


def main() -> int:
    if os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        selected = lanes(set(), os.environ["AI10_NATIVE_LANE"])
    else:
        before, head = os.environ["AI10_BEFORE"], os.environ["GITHUB_SHA"]
        if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (before, head)):
            raise ValueError("ai10_native_changed_refs_invalid")
        git = shutil.which("git")
        if git is None:
            raise ValueError("ai10_native_git_required")
        changed = subprocess.run(  # noqa: S603 - fixed read-only Git operation
            [git, "diff", "--name-only", "--no-renames", "-z", before, head],
            capture_output=True,
            check=False,
        )
        selected = (
            lanes(set(changed.stdout.decode().rstrip("\0").split("\0")))
            if changed.returncode == 0
            else (True, True)
        )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write("stockout=" + str(selected[0]).lower() + "\n")
        output.write("anomaly=" + str(selected[1]).lower() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
