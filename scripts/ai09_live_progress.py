"""Flush bounded real worker progress and independent supervisor heartbeats live."""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, TextIO

SOURCE_PREFIX = b"RETAILOPS_PROGRESS "
MAX_LINE_BYTES = 8192
MAX_ARTIFACT_BYTES = 16 * 1024**2


def _label(value: object, maximum: int = 128) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and re.fullmatch(r"[A-Za-z0-9_./:-]+", value) is not None
    )


class LiveProgress:
    def __init__(
        self,
        phase: str,
        *,
        started: float,
        budget_seconds: int,
        artifact: Path,
        interval_seconds: float = 60,
        stream: TextIO | None = None,
    ) -> None:
        if not 0 < interval_seconds <= 120 or budget_seconds < 1:
            raise ValueError("capacity_live_progress_interval_or_budget")
        self.phase, self.started, self.budget = phase, started, budget_seconds
        self.interval, self.stream = interval_seconds, stream or sys.stdout
        descriptor = os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.artifact = os.fdopen(descriptor, "wb")
        self.bytes_written = self.position = self.source_sequence = 0
        self.pending = b""
        self.dropping_line = False
        self.last_heartbeat = float("-inf")
        self.last_progress_at: float | None = None
        self.last_counter: dict[str, Any] | None = None
        self.stages: list[str] = []
        self.rss = 0
        self.cpu = 0.0
        try:
            self._emit("phase_started")
        except BaseException:
            self.artifact.close()
            raise

    def _emit(self, kind: str, **values: Any) -> None:
        record = {
            "version": "ai09-live-progress-1.0.0",
            "at_utc": datetime.now(UTC).isoformat(),
            "kind": kind,
            "phase": self.phase,
            "active_subphases": self.stages,
            "elapsed_seconds": time.perf_counter() - self.started,
            "budget_seconds": self.budget,
            "sampled_tree_rss_bytes": self.rss,
            "sampled_worker_cpu_seconds_lower_bound": self.cpu,
            **values,
        }
        raw = json.dumps(record, sort_keys=True, allow_nan=False).encode() + b"\n"
        if self.bytes_written + len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("capacity_live_progress_artifact_limit")
        self.artifact.write(raw)
        self.artifact.flush()
        os.fsync(self.artifact.fileno())
        self.bytes_written += len(raw)
        worker = values.get("worker", {})
        counts = worker if "completed" in worker else values.get("last_reported_counter")
        counter = ""
        if counts:
            total = counts.get("total")
            counter = f" {counts['unit']}={counts['completed']}/" + (
                str(total) if total is not None else "unknown"
            )
            if kind == "heartbeat":
                counter = " last_reported_counter_subphase=" + str(counts.get("stage")) + counter
            if counts.get("completed_business_days") is not None:
                counter += (
                    f" days_completed={counts['completed_business_days']}/"
                    f"{counts.get('total_business_days', 'unknown')}"
                )
        stage = worker.get("stage") or (self.stages[-1] if self.stages else "none")
        event = worker.get("event", kind)
        print(
            f"[AI09 LIVE {kind}] phase={self.phase} subphase={stage} event={event} "
            f"elapsed={record['elapsed_seconds']:.1f}/{self.budget}s "
            f"RAM={self.rss / 1024**3:.3f}GiB CPU>={self.cpu:.2f}s{counter}",
            file=self.stream,
            flush=True,
        )

    def _source(self, raw: bytes) -> None:
        value = json.loads(raw)
        allowed = {
            "version",
            "sequence",
            "at_utc",
            "elapsed_seconds",
            "stage",
            "event",
            "active_stages",
            "stage_seconds",
            "error_type",
            "completed",
            "total",
            "unit",
            "business_date",
            "completed_business_days",
            "total_business_days",
            "known_queued_remaining",
        }
        if (
            not isinstance(value, dict)
            or not value.keys() <= allowed
            or value.get("version") != "source-progress-1.0.0"
            or value.get("event")
            not in {"started", "completed", "failed", "progress", "counter_final"}
            or type(value.get("sequence")) is not int
            or value["sequence"] <= self.source_sequence
            or not _label(value.get("stage"))
            or not isinstance(value.get("active_stages"), list)
            or len(value["active_stages"]) > 32
            or any(not _label(v) for v in value["active_stages"])
        ):
            raise ValueError("capacity_live_progress_source_format")
        for name in ("elapsed_seconds", "stage_seconds"):
            if name not in value and name == "stage_seconds":
                continue
            number = value.get(name)
            if (
                not isinstance(number, (int, float))
                or isinstance(number, bool)
                or not math.isfinite(number)
                or number < 0
            ):
                raise ValueError("capacity_live_progress_source_time")
        if not isinstance(value.get("at_utc"), str):
            raise ValueError("capacity_live_progress_source_time")
        stamp = datetime.fromisoformat(value["at_utc"])
        if stamp.utcoffset() != UTC.utcoffset(None):
            raise ValueError("capacity_live_progress_source_time")
        if value.get("business_date") is not None:
            if not isinstance(value["business_date"], str):
                raise ValueError("capacity_live_progress_source_business_date")
            date.fromisoformat(value["business_date"])
        if "error_type" in value and not _label(value["error_type"], 64):
            raise ValueError("capacity_live_progress_source_error_type")
        if value["event"] not in {"progress", "counter_final"} and "completed" in value:
            raise ValueError("capacity_live_progress_counter_format")
        if value["event"] in {"progress", "counter_final"} and (
            type(value.get("completed")) is not int
            or value["completed"] < 0
            or not _label(value.get("unit"), 64)
            or value.get("total") is not None
            and (type(value["total"]) is not int or value["total"] < 0)
        ):
            raise ValueError("capacity_live_progress_counter_format")
        for name in ("completed_business_days", "total_business_days", "known_queued_remaining"):
            number = value.get(name)
            if number is not None and (type(number) is not int or number < 0):
                raise ValueError("capacity_live_progress_counter_format")
        for completed, total in (
            ("completed", "total"),
            ("completed_business_days", "total_business_days"),
        ):
            if value.get(completed) is not None and value.get(total) is not None:
                if value[completed] > value[total]:
                    raise ValueError("capacity_live_progress_counter_exceeds_total")
        self.source_sequence = value["sequence"]
        self.stages = list(value["active_stages"])
        if value["event"] in {"completed", "failed"} and self.stages:
            self.stages.pop()
        if "completed" in value:
            self.last_counter = {
                key: value[key]
                for key in (
                    "stage",
                    "completed",
                    "total",
                    "unit",
                    "completed_business_days",
                    "total_business_days",
                )
                if key in value
            }
        self.last_progress_at = time.perf_counter()
        self._emit("source_event", worker=value)

    def observe(self, log: Path, *, rss_bytes: int, cpu_seconds: float) -> None:
        if (
            type(rss_bytes) is not int
            or rss_bytes < 0
            or not math.isfinite(cpu_seconds)
            or cpu_seconds < 0
        ):
            raise ValueError("capacity_live_progress_resource_format")
        self.rss, self.cpu = rss_bytes, cpu_seconds
        # Read a bounded part of the persisted log; never block on a worker pipe.
        with log.open("rb") as stream:
            if os.fstat(stream.fileno()).st_size < self.position:
                raise ValueError("capacity_live_progress_log_truncated")
            stream.seek(self.position)
            chunk = stream.read(128 * 1024)
            self.position = stream.tell()
        self.pending += chunk
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            if self.dropping_line:
                self.dropping_line = False
            elif line.startswith(SOURCE_PREFIX):
                if len(line) > MAX_LINE_BYTES:
                    raise ValueError("capacity_live_progress_source_line_limit")
                self._source(line[len(SOURCE_PREFIX) :])
        if len(self.pending) > MAX_LINE_BYTES:
            if self.pending.startswith(SOURCE_PREFIX):
                raise ValueError("capacity_live_progress_source_line_limit")
            self.pending = b""
            self.dropping_line = True
        now = time.perf_counter()
        if now - self.last_heartbeat >= self.interval:
            self._emit(
                "heartbeat",
                last_reported_counter=self.last_counter,
                seconds_since_last_source_event=now - self.last_progress_at
                if self.last_progress_at is not None
                else None,
                heartbeat_is_not_work_progress=True,
            )
            self.last_heartbeat = now

    def finish(self, status: str, reason: str | None) -> None:
        try:
            incomplete = self.pending.startswith(SOURCE_PREFIX) or (
                bool(self.pending) and SOURCE_PREFIX.startswith(self.pending)
            )
            if status == "passed" and incomplete:
                self._emit(
                    "phase_finished",
                    status="failed",
                    reason="capacity_live_progress_incomplete_source_event",
                    incomplete_source_event=True,
                )
                raise ValueError("capacity_live_progress_incomplete_source_event")
            self._emit(
                "phase_finished", status=status, reason=reason, incomplete_source_event=incomplete
            )
        finally:
            self.artifact.close()
