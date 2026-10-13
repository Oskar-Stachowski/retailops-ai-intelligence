"""Bounded, data-free phase diagnostics for the disposable read acceptance only."""

import json
import sys
import time

READ_STAGES = frozenset(
    {
        "database_empty",
        "fixture_prepared",
        "inputs_registered",
        "release_seeded",
        "first_publication",
        "server_ready",
        "http_scope_checked",
        "replay_checked",
        "failed_run_checked",
        "outside_history",
        "history_budget_checked",
        "corruption_restored",
        "freshness_probes_started",
        "freshness_probes_complete",
        "final_snapshot",
    }
)


def read_progress(stage: str, started: float, *, completed: int = 0) -> None:
    """Flush fixed phase names and numeric timing, without any fixture or credential."""
    if stage not in READ_STAGES or type(completed) is not int or not 0 <= completed <= 33:
        raise ValueError("invalid_read_acceptance_progress")
    print(
        json.dumps(
            {
                "forecast_read_progress": stage,
                "completed": completed,
                "elapsed_ms": max(0, round((time.monotonic() - started) * 1000)),
            }
        ),
        file=sys.stderr,
        flush=True,
    )


def safe_read_progress(raw: bytes) -> list[dict[str, str | int]]:
    """Decode only bounded, closed-schema diagnostics, including on child timeout."""
    records: list[dict[str, str | int]] = []
    for line in raw[-8192:].splitlines():
        try:
            value = json.loads(line)
        except (ValueError, UnicodeDecodeError, RecursionError):
            continue
        if (
            isinstance(value, dict)
            and set(value) == {"forecast_read_progress", "completed", "elapsed_ms"}
            and isinstance(value["forecast_read_progress"], str)
            and value["forecast_read_progress"] in READ_STAGES
            and type(value["completed"]) is int
            and 0 <= value["completed"] <= 33
            and type(value["elapsed_ms"]) is int
            and 0 <= value["elapsed_ms"] <= 3_600_000
        ):
            records.append(value)
    return records[-32:]
