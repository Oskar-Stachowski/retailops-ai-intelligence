"""Retain failed preparation work and settle interrupted work without inventing cost.

The remote caller must obtain the run, job and final artifact from authenticated
GitHub API requests and verify the downloaded archive before constructing history.
This module validates that evidence; a self-authored hash is not remote authority.
It never dispatches, retries, regenerates data or grants Project/final access.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import preparation_execution as execution
from retailops_ai.source_snapshot.files import read_json

VERSION = "ai09-terminal-preparation-history-1.0.0"
SNAPSHOT_VERSION = "ai09-preparation-attempt-snapshot-1.0.0"
REPOSITORY = "Oskar-Stachowski/retailops-ai-intelligence"
FAILED = {"failure", "cancelled", "timed_out"}
WORKFLOW_JOBS = {
    ".github/workflows/ai09-generation-control.yml": "control",
    ".github/workflows/ai09-development-capacity.yml": "measure",
}


def snapshot(root: Path) -> dict[str, Any]:
    """Capture all records, including the unfinished start, from the owned session."""
    state = execution.inspect(root)
    if state["status"] not in {"failed", "unfinished"}:
        raise ValueError("preparation_attempt_terminal_failure_required")
    return {
        "version": SNAPSHOT_VERSION,
        "identity_sha256": canonical_sha256(state["identity"]),
        "plan_sha256": canonical_sha256(state["plan"]),
        "execution": read_json(root, "execution.json"),
        "events": state["events"],
    }


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ValueError("preparation_attempt_github_timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def terminal_job_bound(proof: dict[str, Any], *, head: str, snapshot_sha256: str) -> float:
    """Full job duration plus timestamp rounding, used only as a conservative reserve."""
    run, job, artifact = (proof.get(k, {}) for k in ("run", "job", "artifact"))
    run_id, attempt, job_id = run.get("id"), run.get("run_attempt"), job.get("id")
    artifact_id = artifact.get("id")
    if (
        proof.get("authenticated_github_download") is not True
        or proof.get("snapshot_sha256") != snapshot_sha256
        or any(
            type(value) is not int or value <= 0 for value in (run_id, attempt, job_id, artifact_id)
        )
        or run.get("repository", {}).get("full_name") != REPOSITORY
        or run.get("head_sha") != head
        or run.get("status") != "completed"
        or run.get("conclusion") not in FAILED
        or run.get("event") != "workflow_dispatch"
        or run.get("path") not in WORKFLOW_JOBS
        or job.get("name") != WORKFLOW_JOBS.get(run.get("path", ""))
        or job.get("run_id") != run_id
        or job.get("run_attempt") != attempt
        or job.get("head_sha") != head
        or job.get("status") != "completed"
        or job.get("conclusion") not in FAILED
        or artifact.get("workflow_run", {}).get("id") != run_id
        or artifact.get("workflow_run", {}).get("head_sha") != head
        or artifact.get("name") != f"ai09-preparation-attempt-{head}-{run_id}-{attempt}"
        or artifact.get("expired") is not False
        or not isinstance(proof.get("downloaded_zip_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", proof["downloaded_zip_sha256"])
        or artifact.get("digest") != "sha256:" + proof["downloaded_zip_sha256"]
    ):
        raise ValueError("preparation_attempt_untrusted_or_nonterminal_remote_history")
    started, ended = _timestamp(job.get("started_at")), _timestamp(job.get("completed_at"))
    created = _timestamp(artifact.get("created_at"))
    if ended <= started or not started <= created <= ended:
        raise ValueError("preparation_attempt_artifact_outside_latest_job")
    return math.ceil((ended - started).total_seconds()) + 2.0


def validate_history(
    history: dict[str, Any],
    *,
    identity: dict[str, Any],
    plan: dict[str, Any],
    events: list[dict[str, Any]],
    charged_wall_seconds: float,
    prior_adjustments: list[dict[str, Any]],
    source_run_id: Any,
) -> dict[str, Any]:
    """Validate the complete snapshot and charge the suffix omitted by checkpoint reuse."""
    captured = history.get("snapshot", {})
    all_events = captured.get("events", [])
    count = len(events)
    header = captured.get("execution", {})
    if (
        history.get("version") != VERSION
        or type(source_run_id) is not int
        or source_run_id <= 0
        or history.get("remote", {}).get("run", {}).get("id") != source_run_id
        or captured.get("version") != SNAPSHOT_VERSION
        or not 4 <= count <= 16
        or count % 4
        or not isinstance(all_events, list)
        or not count < len(all_events) <= count + 4
        or all_events[:count] != events
        or captured.get("identity_sha256") != canonical_sha256(identity)
        or captured.get("plan_sha256") != canonical_sha256(plan)
        or header.get("version") != execution.VERSION
        or header.get("identity_sha256") != canonical_sha256(identity)
        or header.get("plan_sha256") != canonical_sha256(plan)
        or header.get("cost_adjustments", []) != prior_adjustments
        or header.get("project_journal_initialized") is not False
        or header.get("final_test_authorized") is not False
    ):
        raise ValueError("preparation_attempt_incomplete_or_rewritten_history")
    bound = terminal_job_bound(
        history.get("remote", {}),
        head=identity["consumer_commit"],
        snapshot_sha256=canonical_sha256(captured),
    )
    charged, cpu = charged_wall_seconds, 0.0
    previous = canonical_sha256(events[-1])
    failed, unknown_cpu = False, False
    costs = []
    for index, event in enumerate(all_events[count:], start=count):
        cost, sample, failed = execution.validate_event(
            event,
            index=index,
            previous=previous,
            charged=charged,
            budget=plan["budgets"]["wall_seconds"],
            failed=failed,
        )
        charged += cost
        costs.append(cost)
        if sample is None:
            unknown_cpu = True
        else:
            cpu += sample
        previous = canonical_sha256(event)
    interrupted = bool(len(all_events) % 2)
    if not failed and not interrupted:
        raise ValueError("preparation_attempt_older_prefix_would_erase_completed_work")
    # The entire terminated job bounds the unfinished operation. Deliberately
    # do not subtract measured work: over-reservation is safer than an invented
    # exact cost. CPU remains unknown; sampled lower bounds are still retained.
    reserved = bound if interrupted else 0.0
    return {
        "charged_wall_seconds": sum(costs) + reserved,
        "known_suffix_wall_seconds": sum(costs),
        "reserved_unknown_wall_seconds": reserved,
        "worker_cpu_seconds_lower_bound": cpu,
        "unmeasured_cpu_cost_present": unknown_cpu or interrupted,
        "unmeasured_wall_cost_present": interrupted,
        "suffix_events_retained": len(all_events) - count,
        "reservation_is_not_measured_cost": interrupted,
    }


def bind_history(
    previous: Path, *, captured: dict[str, Any], authenticated_remote: dict[str, Any]
) -> dict[str, Any]:
    """Bind independently downloaded final-attempt evidence to the verified prefix."""
    state = execution.inspect(previous)
    if state["status"] != "prepared":
        raise ValueError("preparation_attempt_completed_prefix_required")
    history = {"version": VERSION, "snapshot": captured, "remote": authenticated_remote}
    validate_history(
        history,
        identity=state["identity"],
        plan=state["plan"],
        events=state["events"],
        charged_wall_seconds=state["charged_wall_seconds"],
        prior_adjustments=state["cost_adjustments"],
        source_run_id=authenticated_remote.get("run", {}).get("id"),
    )
    return history
